# Interactive PDF Education Platform

A complete pipeline and viewer system that transforms any PDF book or quiz into an interactive educational experience. Built for Arabic and English content.

## Project Structure

```
├── pipeline/                    # PDF processing scripts
│   ├── extract_pdf.py           # Main extraction pipeline (text, images, structure, search)
│   ├── quiz_detect.py           # Quiz detection (MCQ, fill-in-blank, open-ended)
│   └── skill/                   # Cowork skill definition for the pipeline
├── viewers/                     # Interactive web viewers
│   ├── book-viewer/             # Full book viewer with annotations
│   │   ├── index.html           # Single-file viewer app
│   │   ├── book-data.js         # Embedded book data (base64 images + JSON)
│   │   └── book-data/           # Extracted data (pages, words, images, structure)
│   └── quiz-viewer/             # Interactive quiz viewer
│       ├── index.html           # Single-file quiz app
│       ├── quiz-embedded.js     # Embedded quiz data + page images
│       └── quiz-data.json       # Quiz structure data
└── books/                       # Source PDF files
    ├── WAQF Radiology 2028-Final.pdf
    └── math-quiz-grade4.pdf
```

## Features

### PDF Extraction Pipeline (`extract_pdf.py`)
- Renders all pages as high-resolution images
- Extracts word-level text with bounding box coordinates
- Detects document structure using font-size analysis (generic, works with any PDF)
- Extracts embedded images
- Builds full-text search index
- Outputs a complete data package (ZIP or directory)

### Quiz Detection (`quiz_detect.py`)
- Detects quiz structure in image-based PDFs
- Supports three question types:
  - **MCQ** — Multiple choice with clickable option circles
  - **Fill-in-blank** — Complete the sentence questions
  - **Open-ended** — Word problems and free-response
- Uses pink circle detection and pixel comparison for answer key matching
- Works with Arabic RTL layout

### Book Viewer
- Page-by-page navigation with high-res images
- Word-level text highlighting (5 colors)
- Freehand drawing with pen, eraser, and undo
- Sticky notes with handwriting canvas
- Full-text search across all pages
- Dark mode
- Reading progress tracking
- Keyboard shortcuts
- All data persisted in localStorage

### Quiz Viewer
- Interactive MCQ: click to select answer, double-click to reveal correct answer
- Fill-in-blank & open-ended: handwriting canvas to write answers, then reveal correct answer
- Answer overlay from answer key pages
- Score tracking with progress bar
- Statistics modal
- Drawing tools (pen, eraser, clear)
- All answers and drawings saved to localStorage

## Requirements

```bash
pip install pdfplumber pdf2image Pillow scipy
```

System dependency: **Poppler** (for pdf2image)
```bash
# Ubuntu/Debian
sudo apt-get install poppler-utils

# macOS
brew install poppler
```

## Usage

### Extract a book
```bash
python pipeline/extract_pdf.py INPUT.pdf OUTPUT_DIR
```

### Detect quiz structure
```bash
python pipeline/quiz_detect.py QUIZ.pdf OUTPUT_DIR
```

### View the interactive book
Open `viewers/book-viewer/index.html` in any browser (works offline via `file://`).

### View the interactive quiz
Open `viewers/quiz-viewer/index.html` in any browser (works offline via `file://`).

## How It Works

The system uses a "Smart Image" approach: PDF pages are displayed as high-resolution images with an invisible interactive data layer on top. This preserves the exact visual layout while enabling rich interactions like word highlighting, drawing, and clickable quiz zones.

For quiz detection, question and answer pages are compared pixel-by-pixel to identify where answers were filled in (typically in red ink), then maps these answer zones to the detected question structure.

## License

Educational use.
