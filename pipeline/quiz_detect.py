#!/usr/bin/env python3
"""
Quiz PDF Detector
=================
Detects MCQ, fill-in-blank, and open-ended questions in image-based PDFs
by comparing question pages with answer-key pages.

Works for Arabic and English quiz PDFs where:
- First half = question pages
- Second half = same pages with correct answers highlighted/filled in
"""

import json
import os
import sys
import numpy as np
from pdf2image import convert_from_path
from scipy import ndimage
from scipy.ndimage import binary_dilation

def render_page(pdf_path, page_num, dpi=200):
    imgs = convert_from_path(pdf_path, dpi=dpi, first_page=page_num, last_page=page_num)
    return np.array(imgs[0])

def detect_all_pink_circles(img):
    """Find ALL pink/magenta circles on the page, classified by size and position.
    Returns (large_right, small_right, option_markers) where:
    - large_right: big circles at far right (question numbers on MCQ/open pages)
    - small_right: smaller circles at far right (question numbers on fill-in pages)
    - option_markers: medium circles NOT at far right (أ/ب/ج/د options)
    """
    h, w = img.shape[:2]
    r, g, b = img[:,:,0].astype(int), img[:,:,1].astype(int), img[:,:,2].astype(int)

    pink = (r > 170) & (g < 120) & (b > 60) & ((r - g) > 80)

    labeled, n = ndimage.label(pink)

    large_right = []   # area > 900, cx > 89%  → Q numbers (MCQ/open pages)
    small_right = []   # area 400-900, cx > 89% → Q numbers (fill-in pages)
    option_markers = [] # area 400-900, cx < 89% → option circles

    for i in range(1, n + 1):
        ys, xs = np.where(labeled == i)
        area = len(ys)
        if area < 400:
            continue

        cx = float(xs.mean()) / w * 100
        cy = float(ys.mean()) / h * 100

        info = {
            "cy": cy,
            "y0": float(ys.min()) / h * 100,
            "y1": float(ys.max()) / h * 100,
            "cx": cx,
            "x0": float(xs.min()) / w * 100,
            "x1": float(xs.max()) / w * 100,
            "area": area,
        }

        if cx > 89:
            # Far right — question number circle
            if area > 900:
                large_right.append(info)
            else:
                small_right.append(info)
        elif cx > 5:
            # Not far right, not left margin — option marker
            if area <= 900:
                option_markers.append(info)

    # Sort by Y position
    large_right.sort(key=lambda c: c["cy"])
    small_right.sort(key=lambda c: c["cy"])
    option_markers.sort(key=lambda m: (m["cy"], m["cx"]))

    # Deduplicate option markers
    deduped = []
    for m in option_markers:
        is_dup = False
        for existing in deduped:
            if abs(m["cy"] - existing["cy"]) < 2 and abs(m["cx"] - existing["cx"]) < 3:
                is_dup = True
                break
        if not is_dup:
            deduped.append(m)

    return large_right, small_right, deduped

def find_answer_highlights(q_img, a_img):
    """Find regions that differ between question and answer page."""
    h, w = q_img.shape[:2]
    diff = np.abs(q_img.astype(int) - a_img.astype(int)).sum(axis=2)
    changed = diff > 80
    changed = binary_dilation(changed, iterations=4)
    labeled, n = ndimage.label(changed)

    zones = []
    for i in range(1, n + 1):
        ys, xs = np.where(labeled == i)
        if len(ys) < 30:
            continue
        zones.append({
            "cy": float(ys.mean()) / h * 100,
            "cx": float(xs.mean()) / w * 100,
            "y0": float(ys.min()) / h * 100,
            "y1": float(ys.max()) / h * 100,
            "x0": float(xs.min()) / w * 100,
            "x1": float(xs.max()) / w * 100,
        })
    return zones

def build_mcq_questions(q_circles, opt_markers, answer_zones):
    """Build MCQ question data with clickable option zones and correct answers."""
    questions = []

    for qi, qc in enumerate(q_circles):
        y_top = qc["y0"] - 1
        if qi + 1 < len(q_circles):
            y_bot = q_circles[qi + 1]["y0"] - 0.5
        else:
            y_bot = qc["y1"] + 10

        # Find option markers in this question's Y range
        opts = [m for m in opt_markers if y_top <= m["cy"] <= y_bot]
        opts.sort(key=lambda o: -o["cx"])  # right to left for Arabic

        option_zones = []
        for oi, opt in enumerate(opts[:4]):
            radius_x = 2.5
            radius_y = 2.0
            option_zones.append({
                "label": ["أ", "ب", "ج", "د"][oi] if oi < 4 else f"opt{oi}",
                "index": oi,
                "x0": round(opt["cx"] - radius_x, 2),
                "y0": round(opt["cy"] - radius_y, 2),
                "x1": round(opt["cx"] + radius_x, 2),
                "y1": round(opt["cy"] + radius_y, 2),
                "cx": round(opt["cx"], 2),
                "cy": round(opt["cy"], 2),
            })

        # Find correct answer
        correct_idx = None
        if answer_zones and option_zones:
            for az in answer_zones:
                if abs(az["cy"] - qc["cy"]) > (y_bot - y_top):
                    continue
                best_dist = 999
                best_oi = None
                for oi, opt in enumerate(option_zones):
                    dist = abs(az["cy"] - opt["cy"]) + abs(az["cx"] - opt["cx"]) * 0.5
                    if dist < best_dist:
                        best_dist = dist
                        best_oi = oi
                if best_oi is not None and best_dist < 15:
                    correct_idx = best_oi

        questions.append({
            "number": qi + 1,
            "y0": round(y_top, 2),
            "y1": round(y_bot, 2),
            "cy": round(qc["cy"], 2),
            "options": option_zones,
            "correct": correct_idx,
        })

    return questions

def build_open_questions(q_circles, page_bottom_pct=95.0):
    """Build open/fill-in question data with clickable reveal zones.
    Each question is a row from its circle to the next circle.
    Students click the row to reveal the answer from the answer page."""
    questions = []

    for qi, qc in enumerate(q_circles):
        y_top = qc["y0"] - 1
        if qi + 1 < len(q_circles):
            y_bot = q_circles[qi + 1]["y0"] - 0.5
        else:
            y_bot = min(qc["y1"] + 15, page_bottom_pct)

        questions.append({
            "number": qi + 1,
            "y0": round(y_top, 2),
            "y1": round(y_bot, 2),
            "cy": round(qc["cy"], 2),
            "options": [],
            "correct": None,
        })

    return questions

def classify_page_type(large_right, small_right, opt_markers):
    """Determine page type based on detected circles.

    MCQ pages: have option markers (أ/ب/ج/د circles across the page)
    Fill-in pages: have small question number circles at right, no option markers
    Open pages: have large question number circles at right, no option markers
    """
    if len(opt_markers) > 0:
        return "mcq"

    # No option markers — it's either fill-in-blank or open-ended
    # Fill-in pages have many small circles (one per line), open pages have fewer larger circles
    if len(small_right) > 5:
        return "fill_in_blank"
    elif len(large_right) > 0:
        return "open_ended"
    else:
        return "open_ended"

def process_quiz_pdf(pdf_path, output_dir, dpi=200):
    """Main function: detect quiz structure and output quiz-data.json."""
    print(f"Analyzing quiz PDF: {os.path.basename(pdf_path)}")

    from pdf2image.pdf2image import pdfinfo_from_path
    info = pdfinfo_from_path(pdf_path)
    total_pages = info["Pages"]
    print(f"Total pages: {total_pages}")

    mid = total_pages // 2

    # Detect Q/A split
    q_page1 = render_page(pdf_path, 2, dpi)
    best_match_offset = 0
    best_match_score = 0
    for offset in range(mid - 2, mid + 3):
        if offset < 2 or offset > total_pages:
            continue
        try:
            test_page = render_page(pdf_path, offset + 1, dpi)
            if test_page.shape == q_page1.shape:
                diff = np.abs(q_page1.astype(int) - test_page.astype(int)).mean()
                similarity = max(0, 100 - diff)
                if similarity > best_match_score:
                    best_match_score = similarity
                    best_match_offset = offset
        except:
            continue

    answer_start = mid + 2
    q_start = 2
    q_end = mid + 1

    print(f"Question pages: {q_start}-{q_end}")
    print(f"Answer pages: {answer_start}-{total_pages}")

    all_quiz_data = {
        "source": os.path.basename(pdf_path),
        "total_pages": total_pages,
        "question_pages": list(range(q_start, q_end + 1)),
        "answer_pages": list(range(answer_start, total_pages + 1)),
        "pages": {}
    }

    for qi, q_page_num in enumerate(range(q_start, q_end + 1)):
        a_page_num = answer_start + qi
        if a_page_num > total_pages:
            break

        print(f"\n  Processing page {q_page_num} (answers on page {a_page_num})...")

        q_img = render_page(pdf_path, q_page_num, dpi)
        a_img = render_page(pdf_path, a_page_num, dpi)

        # Detect all pink circles
        large_right, small_right, opt_markers = detect_all_pink_circles(q_img)

        # Classify page type
        page_type = classify_page_type(large_right, small_right, opt_markers)

        print(f"    Type: {page_type}")
        print(f"    Large Q circles: {len(large_right)}, Small Q circles: {len(small_right)}, Option markers: {len(opt_markers)}")

        # Detect answer highlights
        if q_img.shape == a_img.shape:
            answer_zones = find_answer_highlights(q_img, a_img)
            print(f"    Answer zones: {len(answer_zones)}")
        else:
            answer_zones = []

        if page_type == "mcq":
            # MCQ: use large circles as Q numbers, option markers for choices
            questions = build_mcq_questions(large_right, opt_markers, answer_zones)
            answered = sum(1 for q in questions if q["correct"] is not None)
            print(f"    Questions: {len(questions)}, with answers: {answered}")

        elif page_type == "fill_in_blank":
            # Fill-in: use small_right circles as question numbers
            # Skip header-area circles (cy < 8%) — those are decorative elements
            q_circles = [c for c in small_right if c["cy"] > 8]
            if not q_circles:
                q_circles = small_right  # fallback

            # Also include any large circles that are actual questions (not headers)
            # These appear on mixed pages (e.g. page 9 has fill-in top + open bottom)
            open_qs = [c for c in large_right if c["cy"] > 8]
            # Only include large circles below the last small circle (mixed pages)
            if q_circles and open_qs:
                last_small_y = max(c["cy"] for c in q_circles)
                extra_qs = [c for c in open_qs if c["cy"] > last_small_y]
                q_circles = q_circles + extra_qs

            questions = build_open_questions(q_circles)
            print(f"    Questions: {len(questions)}")

        else:
            # Open-ended: use large circles as Q numbers, skip header circles
            q_circles = [c for c in large_right if c["cy"] > 8]
            if not q_circles and large_right:
                q_circles = large_right  # fallback
            questions = build_open_questions(q_circles)
            print(f"    Questions: {len(questions)}")

        all_quiz_data["pages"][str(q_page_num)] = {
            "page": q_page_num,
            "answer_page": a_page_num,
            "type": page_type,
            "questions": questions,
        }

    # Save quiz data
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, "quiz-data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_quiz_data, f, ensure_ascii=False, indent=2)

    total_q = sum(len(p["questions"]) for p in all_quiz_data["pages"].values())
    total_mcq = sum(
        sum(1 for q in p["questions"] if q["correct"] is not None)
        for p in all_quiz_data["pages"].values()
    )
    print(f"\n{'='*50}")
    print(f"Quiz detection complete!")
    print(f"  Total questions: {total_q}")
    print(f"  MCQ with answers: {total_mcq}")
    print(f"  Saved to: {out_path}")
    print(f"{'='*50}")

    return all_quiz_data

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python quiz_detect.py INPUT.pdf OUTPUT_DIR")
        sys.exit(1)
    process_quiz_pdf(sys.argv[1], sys.argv[2])
