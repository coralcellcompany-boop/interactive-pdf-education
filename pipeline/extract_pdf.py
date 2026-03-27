#!/usr/bin/env python3
"""
PDF Data Extraction Pipeline
=============================
Extracts complete structured data from any PDF:
  - Page images (WebP/PNG/JPG at configurable DPI)
  - Word-level text with bounding boxes (percentage coordinates)
  - Embedded images
  - Auto-detected book structure (chapters/sections)
  - Search index
  - Full text
  - Manifest with metadata

Usage:
    python3 extract_pdf.py INPUT.pdf OUTPUT_DIR [OPTIONS]
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import warnings
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------

def check_dependencies():
    """Verify all required tools and packages are available."""
    missing = []
    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        missing.append("pdfplumber (pip install pdfplumber)")
    try:
        from pdf2image import convert_from_path  # noqa: F401
    except ImportError:
        missing.append("pdf2image (pip install pdf2image)")
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        missing.append("Pillow (pip install Pillow)")
    if shutil.which("pdfimages") is None:
        missing.append("poppler-utils (apt install poppler-utils)")
    if missing:
        print("ERROR: Missing dependencies:")
        for m in missing:
            print(f"  - {m}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# 1. Page image rendering
# ---------------------------------------------------------------------------

def render_pages(pdf_path, out_dir, dpi=200, fmt="webp"):
    """Render every page of the PDF as an image."""
    from pdf2image import convert_from_path
    pages_dir = os.path.join(out_dir, "book-data", "pages")
    os.makedirs(pages_dir, exist_ok=True)

    print(f"[1/6] Rendering pages at {dpi} DPI as {fmt.upper()}...")
    images = convert_from_path(pdf_path, dpi=dpi, fmt="png", thread_count=4)
    total = len(images)
    files = []
    for i, img in enumerate(images, 1):
        fname = f"page-{i:03d}.{fmt}"
        fpath = os.path.join(pages_dir, fname)
        if fmt == "webp":
            img.save(fpath, "WEBP", quality=85)
        elif fmt == "png":
            img.save(fpath, "PNG")
        elif fmt in ("jpg", "jpeg"):
            img.save(fpath, "JPEG", quality=90)
        files.append(fname)
        if i % 20 == 0 or i == total:
            print(f"       Rendered {i}/{total} pages")
    print(f"       Done — {total} page images saved")
    return files, total


# ---------------------------------------------------------------------------
# 2. Word-level text extraction
# ---------------------------------------------------------------------------

def extract_words(pdf_path, out_dir):
    """Extract word-level text with bounding boxes from every page."""
    import pdfplumber

    words_dir = os.path.join(out_dir, "book-data", "words")
    os.makedirs(words_dir, exist_ok=True)

    print("[2/6] Extracting word-level text with bounding boxes...")
    pdf = pdfplumber.open(pdf_path)
    total_pages = len(pdf.pages)
    total_words = 0
    total_img_regions = 0
    page_data_list = []

    for i, page in enumerate(pdf.pages, 1):
        pw = float(page.width)
        ph = float(page.height)
        raw_words = page.extract_words(keep_blank_chars=False, extra_attrs=["fontname", "size"])
        raw_images = page.images

        # Words as percentage coordinates, with font info for structure detection
        words = []
        for w in raw_words:
            text = w.get("text", "").strip()
            if not text:
                continue
            word_obj = {
                "t": text,
                "x0": round(float(w["x0"]) / pw * 100, 3),
                "y0": round(float(w["top"]) / ph * 100, 3),
                "x1": round(float(w["x1"]) / pw * 100, 3),
                "y1": round(float(w["bottom"]) / ph * 100, 3),
            }
            # Store font info for structure detection (not saved to JSON)
            font_size = None
            try:
                font_size = float(w.get("size", 0))
            except (TypeError, ValueError):
                pass
            word_obj["_font_size"] = font_size
            word_obj["_font_name"] = w.get("fontname", "")
            words.append(word_obj)

        # Image regions as percentage coordinates
        img_regions = []
        for im in raw_images:
            img_regions.append({
                "x0": round(float(im["x0"]) / pw * 100, 2),
                "y0": round(float(im["top"]) / ph * 100, 2),
                "x1": round(float(im["x1"]) / pw * 100, 2),
                "y1": round(float(im["bottom"]) / ph * 100, 2),
            })

        # Full text for this page
        full_text = page.extract_text() or ""

        page_obj = {
            "page": i,
            "width": pw,
            "height": ph,
            "words": words,
            "images": img_regions,
            "full_text": full_text,
        }

        # Save JSON without internal _font fields
        clean_words = [{k: v for k, v in w.items() if not k.startswith("_")} for w in words]
        save_obj = {**page_obj, "words": clean_words}
        fname = f"page-{i:03d}.json"
        with open(os.path.join(words_dir, fname), "w", encoding="utf-8") as f:
            json.dump(save_obj, f, ensure_ascii=False)

        total_words += len(words)
        total_img_regions += len(img_regions)
        page_data_list.append(page_obj)

        if i % 20 == 0 or i == total_pages:
            print(f"       Processed {i}/{total_pages} pages")

    pdf.close()
    print(f"       Done — {total_words} words, {total_img_regions} image regions across {total_pages} pages")
    return page_data_list, total_words, total_img_regions


# ---------------------------------------------------------------------------
# 3. Embedded image extraction
# ---------------------------------------------------------------------------

def extract_images(pdf_path, out_dir):
    """Extract embedded images using poppler's pdfimages tool."""
    images_dir = os.path.join(out_dir, "book-data", "images")
    os.makedirs(images_dir, exist_ok=True)

    print("[3/6] Extracting embedded images...")

    if shutil.which("pdfimages") is None:
        print("       WARNING: pdfimages not found — skipping image extraction")
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, "img")
        subprocess.run(
            ["pdfimages", "-all", pdf_path, prefix],
            capture_output=True
        )
        # Also extract as PPM for images that didn't come out as png/jpg
        subprocess.run(
            ["pdfimages", pdf_path, os.path.join(tmp, "ppm")],
            capture_output=True
        )

        # Collect all extracted files
        raw_files = sorted(Path(tmp).glob("img-*.*"))
        ppm_files = sorted(Path(tmp).glob("ppm-*.*"))

        # Map image index to page number using pdfimages -list
        result = subprocess.run(
            ["pdfimages", "-list", pdf_path],
            capture_output=True, text=True
        )
        page_map = {}  # image_index -> page_number
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n")[2:]:  # skip header
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        pg = int(parts[0])
                        idx = int(parts[1])
                        page_map[idx] = pg
                    except ValueError:
                        continue

        # Process and filter images
        from PIL import Image
        kept = 0
        page_counters = Counter()
        min_size = 50  # minimum dimension in pixels to keep

        for fpath in raw_files:
            try:
                # Parse index from filename like img-000.png
                idx_str = fpath.stem.split("-")[-1]
                idx = int(idx_str)
            except (ValueError, IndexError):
                continue

            page_num = page_map.get(idx, 0)

            # Try to open and filter
            try:
                img = Image.open(fpath)
                w, h = img.size
                if w < min_size and h < min_size:
                    continue  # skip tiny images

                page_counters[page_num] += 1
                count = page_counters[page_num]
                ext = fpath.suffix.lower()
                if ext == ".ppm":
                    ext = ".png"
                    out_name = f"page-{page_num:03d}-img-{count:02d}.png"
                    out_path = os.path.join(images_dir, out_name)
                    img.save(out_path, "PNG")
                else:
                    if ext not in (".png", ".jpg", ".jpeg"):
                        ext = ".png"
                    out_name = f"page-{page_num:03d}-img-{count:02d}{ext}"
                    out_path = os.path.join(images_dir, out_name)
                    shutil.copy2(fpath, out_path)
                kept += 1
            except Exception:
                continue

        # Also process PPM files that might not have been in the -all set
        for fpath in ppm_files:
            try:
                idx_str = fpath.stem.split("-")[-1]
                idx = int(idx_str)
            except (ValueError, IndexError):
                continue

            page_num = page_map.get(idx, 0)
            # Check if we already have this page+index
            expected_prefix = f"page-{page_num:03d}-img-"
            existing = [f for f in os.listdir(images_dir) if f.startswith(expected_prefix)]
            # Only add if the count for this page hasn't been filled by -all
            if idx in [int(f.stem.split("-")[-1]) for f in raw_files if f.stem.split("-")[-1].isdigit()]:
                continue  # already handled

            try:
                img = Image.open(fpath)
                w, h = img.size
                if w < min_size and h < min_size:
                    continue
                page_counters[page_num] += 1
                count = page_counters[page_num]
                out_name = f"page-{page_num:03d}-img-{count:02d}.png"
                out_path = os.path.join(images_dir, out_name)
                img.save(out_path, "PNG")
                kept += 1
            except Exception:
                continue

    print(f"       Done — {kept} images extracted")
    return kept


# ---------------------------------------------------------------------------
# 4. Structure detection
# ---------------------------------------------------------------------------

def detect_structure(pdf_path, page_data_list, title_override=None, author_override=None, lang_override=None):
    """
    Auto-detect document structure using font-size analysis.

    This is fully generic — works for any PDF (textbooks, reports, manuals,
    prep books, school materials, etc.) in any language. Instead of looking
    for specific keywords like "Chapter" or "Lecture", it analyzes font sizes
    to find what the document itself treats as headings.
    """
    import pdfplumber

    print("[4/6] Detecting document structure...")

    pdf = pdfplumber.open(pdf_path)
    meta = pdf.metadata or {}
    total_pages = len(pdf.pages)

    # --- Title detection ---
    title = title_override
    if not title:
        title = meta.get("Title", "")
        if not title and page_data_list:
            # Use the largest text on page 1
            p1 = page_data_list[0]
            if p1["words"]:
                max_size = 0
                biggest_words = []
                for w in p1["words"]:
                    fs = w.get("_font_size") or 0
                    if fs > max_size:
                        max_size = fs
                        biggest_words = [w["t"]]
                    elif fs == max_size:
                        biggest_words.append(w["t"])
                title = " ".join(biggest_words)[:150] if biggest_words else ""
            if not title:
                title = p1["full_text"].split("\n")[0][:100] if p1.get("full_text") else ""
    title = title.strip() if title else "Untitled Document"

    # --- Author detection ---
    author = author_override
    if not author:
        author = meta.get("Author", "")
    author = author.strip() if author else "Unknown"

    # --- Language detection ---
    language = lang_override
    if not language:
        arabic_count = 0
        latin_count = 0
        cjk_count = 0
        cyrillic_count = 0
        for pg_data in page_data_list[:20]:
            for w in pg_data["words"]:
                for ch in w["t"]:
                    if len(ch) != 1:
                        continue
                    cp = ord(ch)
                    if 0x0600 <= cp <= 0x06FF:
                        arabic_count += 1
                    elif 0x0041 <= cp <= 0x007A:
                        latin_count += 1
                    elif 0x4E00 <= cp <= 0x9FFF:
                        cjk_count += 1
                    elif 0x0400 <= cp <= 0x04FF:
                        cyrillic_count += 1
        langs = []
        if latin_count > 50:
            langs.append("English")
        if arabic_count > 50:
            langs.append("Arabic")
        if cjk_count > 50:
            langs.append("CJK")
        if cyrillic_count > 50:
            langs.append("Cyrillic")
        language = langs if langs else ["Unknown"]

    # =========================================================================
    # GENERIC STRUCTURE DETECTION via font-size analysis
    # =========================================================================
    #
    # The idea: every document uses font sizes to signal hierarchy.
    # The body text uses the most common font size. Anything significantly
    # larger, appearing in the top portion of a page, is likely a heading.
    # We don't need to know the language or keyword — just the visual signal.

    # Step 1: Collect all font sizes across the document to find the "body" size
    all_sizes = []
    for pg_data in page_data_list:
        for w in pg_data["words"]:
            fs = w.get("_font_size")
            if fs and fs > 0:
                all_sizes.append(round(fs, 1))

    if not all_sizes:
        # No font info available (scanned PDF) — fall back to text patterns
        body_size = 0
        heading_threshold = 0
    else:
        size_counts = Counter(all_sizes)
        body_size = size_counts.most_common(1)[0][0]  # most frequent = body text
        # A heading is text that's at least 1.3x the body size
        heading_threshold = body_size * 1.3

    # Step 2: For each page, check if it starts with large text (a heading)
    chapters = []
    sections = []
    page_metadata = []

    for pg_data in page_data_list:
        pg = pg_data["page"]
        word_count = len(pg_data["words"])
        image_count = len(pg_data["images"])
        text = pg_data["full_text"].strip()
        first_line = text.split("\n")[0].strip() if text else ""

        page_type = "content"
        chapter_info = None

        # --- Basic page classification ---
        if pg <= 2 and word_count < 100:
            page_type = "cover"
        elif word_count == 0 and image_count == 0:
            page_type = "blank"
        elif word_count == 0 and image_count > 0:
            page_type = "image_page"
        else:
            # --- Heading detection using font sizes ---
            # Look at words in the top 30% of the page for large text
            top_words = [w for w in pg_data["words"] if w["y0"] < 30]

            if heading_threshold > 0 and top_words:
                # Find the largest font in the top portion
                large_words = []
                for w in top_words:
                    fs = w.get("_font_size") or 0
                    if fs >= heading_threshold:
                        large_words.append(w)

                if large_words:
                    # This page has a heading — extract the heading text
                    # Group large words by their y-position (same line)
                    heading_lines = defaultdict(list)
                    for w in large_words:
                        # Round y0 to group words on the same line
                        y_key = round(w["y0"], 0)
                        heading_lines[y_key].append(w)

                    # Build heading text from the large words
                    heading_parts = []
                    for y_key in sorted(heading_lines.keys()):
                        line_words = sorted(heading_lines[y_key], key=lambda w: w["x0"])
                        line_text = " ".join(w["t"] for w in line_words)
                        heading_parts.append(line_text)

                    heading_text = " — ".join(heading_parts).strip()

                    if heading_text and len(heading_text) > 1:
                        page_type = "section_start"
                        chapter_info = heading_text[:150]

            # --- TOC detection (generic) ---
            if page_type == "content":
                dot_count = text.count("...")
                # Also check for "......." patterns and tab-aligned numbers
                dotdot_count = len(re.findall(r"\.{3,}", text))
                num_at_end = len(re.findall(r"\d+\s*$", text, re.MULTILINE))
                if dotdot_count > 5 or (num_at_end > 8 and word_count < 300):
                    page_type = "toc"

        page_metadata.append({
            "page": pg,
            "type": page_type,
            "word_count": word_count,
            "image_count": image_count,
            "first_line": first_line[:120],
        })

        if page_type == "section_start" and chapter_info:
            chapters.append({
                "title": chapter_info,
                "start_page": pg,
                "end_page": None,
            })

    # Step 3: Filter out false positives
    # If we detected too many "chapters" (more than 1 per 3 pages on average),
    # it means normal sub-headings are being caught. Raise the threshold and retry.
    if len(chapters) > total_pages / 3 and heading_threshold > 0:
        # Too many — only keep the LARGEST headings (top-level structure)
        chapter_sizes = []
        for ch in chapters:
            pg_idx = ch["start_page"] - 1
            if pg_idx < len(page_data_list):
                pg_data = page_data_list[pg_idx]
                max_fs = max((w.get("_font_size") or 0) for w in pg_data["words"] if w["y0"] < 30) if pg_data["words"] else 0
                chapter_sizes.append((ch, max_fs))

        if chapter_sizes:
            sizes_only = [s for _, s in chapter_sizes if s > 0]
            if sizes_only:
                # Keep only pages where heading is in the top 30% of sizes found
                top_size_threshold = sorted(sizes_only, reverse=True)[min(len(sizes_only) // 3, len(sizes_only) - 1)]
                filtered = [ch for ch, sz in chapter_sizes if sz >= top_size_threshold]
                if 2 <= len(filtered) <= total_pages / 3:
                    chapters = filtered
                    # Update page_metadata
                    chapter_pages = {ch["start_page"] for ch in chapters}
                    for pm in page_metadata:
                        if pm["type"] == "section_start" and pm["page"] not in chapter_pages:
                            pm["type"] = "content"

    # Step 4: Fill chapter end pages
    for i, ch in enumerate(chapters):
        if i + 1 < len(chapters):
            ch["end_page"] = chapters[i + 1]["start_page"] - 1
        else:
            ch["end_page"] = total_pages

    # Step 5: Detect special sections (cover, TOC, blank)
    for pm in page_metadata:
        if pm["type"] in ("cover", "toc", "blank"):
            sections.append({
                "type": pm["type"],
                "pages": [pm["page"]],
                "title": pm["type"].replace("_", " ").title(),
            })

    # Merge consecutive same-type sections
    merged_sections = []
    for s in sections:
        if merged_sections and merged_sections[-1]["type"] == s["type"]:
            merged_sections[-1]["pages"].append(s["pages"][0])
        else:
            merged_sections.append(s)

    structure = {
        "title": title,
        "author": author,
        "language": language if isinstance(language, list) else [language],
        "total_pages": total_pages,
        "creator": meta.get("Creator", "Unknown"),
        "sections": merged_sections,
        "chapters": chapters,
        "page_metadata": page_metadata,
    }

    pdf.close()

    ch_count = len(chapters)
    print(f"       Done — detected {ch_count} sections/chapters, {len(merged_sections)} special pages")
    print(f"       Body font size: {body_size}pt, heading threshold: {heading_threshold:.1f}pt")
    return structure


# ---------------------------------------------------------------------------
# 5. Search index
# ---------------------------------------------------------------------------

def build_search_index(page_data_list):
    """Build a flat searchable index of all words with positions."""
    print("[5/6] Building search index...")

    index = []
    for pd in page_data_list:
        pg = pd["page"]
        for w in pd["words"]:
            word = w["t"].lower().strip()
            # Skip very short non-meaningful tokens
            if len(word) < 2 and not word.isalpha():
                continue
            index.append({
                "word": word,
                "page": pg,
                "x0": w["x0"],
                "y0": w["y0"],
                "x1": w["x1"],
                "y1": w["y1"],
            })

    print(f"       Done — {len(index)} searchable entries")
    return index


# ---------------------------------------------------------------------------
# 6. Full text extraction
# ---------------------------------------------------------------------------

def build_full_text(page_data_list, structure):
    """Build organized full text output."""
    print("[6/6] Building full text...")

    lines = []
    lines.append(f"# {structure['title']}")
    lines.append(f"# Author: {structure['author']}")
    lines.append(f"# Pages: {structure['total_pages']}")
    lines.append("")

    # Map pages to chapters
    page_to_chapter = {}
    for ch in structure.get("chapters", []):
        for pg in range(ch["start_page"], (ch.get("end_page") or ch["start_page"]) + 1):
            page_to_chapter[pg] = ch["title"]

    current_chapter = None
    for pd in page_data_list:
        pg = pd["page"]
        ch = page_to_chapter.get(pg)
        if ch and ch != current_chapter:
            current_chapter = ch
            lines.append(f"\n{'='*60}")
            lines.append(f"## {ch}")
            lines.append(f"{'='*60}\n")

        text = pd["full_text"].strip()
        if text:
            lines.append(f"--- Page {pg} ---")
            lines.append(text)
            lines.append("")

    full_text = "\n".join(lines)
    print(f"       Done — {len(full_text)} characters")
    return full_text


# ---------------------------------------------------------------------------
# Manifest generation
# ---------------------------------------------------------------------------

def build_manifest(pdf_path, structure, total_words, total_images, total_img_regions,
                   page_files, dpi, fmt):
    """Build the master manifest.json."""
    return {
        "schema_version": "1.0",
        "source_pdf": os.path.basename(pdf_path),
        "title": structure["title"],
        "author": structure["author"],
        "language": structure["language"],
        "total_pages": structure["total_pages"],
        "total_words": total_words,
        "total_images": total_images,
        "total_image_regions": total_img_regions,
        "chapters_detected": len(structure.get("chapters", [])),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "settings": {
            "dpi": dpi,
            "image_format": fmt,
        },
        "files": {
            "pages": page_files,
            "words": [f"page-{i:03d}.json" for i in range(1, structure["total_pages"] + 1)],
            "structure": "book-structure.json",
            "search_index": "search-index.json",
            "full_text": "full-text.txt",
        }
    }


# ---------------------------------------------------------------------------
# ZIP packaging
# ---------------------------------------------------------------------------

def create_zip(out_dir, pdf_name):
    """Create a ZIP archive of the book-data folder."""
    book_dir = os.path.join(out_dir, "book-data")
    base_name = os.path.splitext(pdf_name)[0]
    zip_name = f"{base_name}-data.zip"
    zip_path = os.path.join(out_dir, zip_name)

    print(f"\nPackaging into {zip_name}...")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(book_dir):
            for file in sorted(files):
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, out_dir)
                zf.write(file_path, arcname)

    size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    print(f"Done — {zip_name} ({size_mb:.1f} MB)")
    return zip_path, zip_name


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="PDF Data Extraction Pipeline")
    parser.add_argument("input_pdf", help="Path to the input PDF file")
    parser.add_argument("output_dir", help="Output directory for extracted data")
    parser.add_argument("--dpi", type=int, default=200, help="DPI for page rendering (default: 200)")
    parser.add_argument("--format", default="webp", choices=["webp", "png", "jpg"],
                        help="Image format for pages (default: webp)")
    parser.add_argument("--no-images", action="store_true", help="Skip embedded image extraction")
    parser.add_argument("--no-zip", action="store_true", help="Don't create ZIP archive")
    parser.add_argument("--title", default=None, help="Override detected title")
    parser.add_argument("--author", default=None, help="Override detected author")
    parser.add_argument("--lang", default=None, help="Override detected language")
    args = parser.parse_args()

    # Validate input
    if not os.path.isfile(args.input_pdf):
        print(f"ERROR: File not found: {args.input_pdf}")
        sys.exit(1)

    pdf_name = os.path.basename(args.input_pdf)
    print(f"\n{'='*60}")
    print(f"  PDF Data Extraction Pipeline")
    print(f"  Input: {pdf_name}")
    print(f"  Output: {args.output_dir}/book-data/")
    print(f"  Settings: {args.dpi} DPI, {args.format.upper()}")
    print(f"{'='*60}\n")

    # Check dependencies
    check_dependencies()

    # Create output directory
    os.makedirs(os.path.join(args.output_dir, "book-data"), exist_ok=True)

    # Step 1: Render pages
    page_files, total_pages = render_pages(args.input_pdf, args.output_dir, args.dpi, args.format)

    # Step 2: Extract words
    page_data_list, total_words, total_img_regions = extract_words(args.input_pdf, args.output_dir)

    # Step 3: Extract embedded images
    total_images = 0
    if not args.no_images:
        total_images = extract_images(args.input_pdf, args.output_dir)
    else:
        print("[3/6] Skipping embedded image extraction (--no-images)")

    # Step 4: Detect structure
    structure = detect_structure(
        args.input_pdf, page_data_list,
        title_override=args.title,
        author_override=args.author,
        lang_override=args.lang,
    )

    # Step 5: Build search index
    search_index = build_search_index(page_data_list)

    # Step 6: Build full text
    full_text = build_full_text(page_data_list, structure)

    # Save JSON files
    data_dir = os.path.join(args.output_dir, "book-data")

    with open(os.path.join(data_dir, "book-structure.json"), "w", encoding="utf-8") as f:
        json.dump(structure, f, ensure_ascii=False, indent=2)

    with open(os.path.join(data_dir, "search-index.json"), "w", encoding="utf-8") as f:
        json.dump(search_index, f, ensure_ascii=False)

    with open(os.path.join(data_dir, "full-text.txt"), "w", encoding="utf-8") as f:
        f.write(full_text)

    # Build and save manifest
    manifest = build_manifest(
        args.input_pdf, structure, total_words, total_images,
        total_img_regions, page_files, args.dpi, args.format,
    )
    with open(os.path.join(data_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    # Package as ZIP
    if not args.no_zip:
        zip_path, zip_name = create_zip(args.output_dir, pdf_name)

    # Summary
    print(f"\n{'='*60}")
    print(f"  EXTRACTION COMPLETE")
    print(f"{'='*60}")
    print(f"  Pages:          {total_pages}")
    print(f"  Words:          {total_words}")
    print(f"  Image regions:  {total_img_regions}")
    print(f"  Embedded imgs:  {total_images}")
    print(f"  Chapters:       {len(structure.get('chapters', []))}")
    print(f"  Search entries: {len(search_index)}")
    print(f"  Language:       {', '.join(structure['language'])}")
    if not args.no_zip:
        print(f"  ZIP:            {zip_name}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
