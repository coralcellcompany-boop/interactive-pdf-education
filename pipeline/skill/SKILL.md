---
name: pdf-extract
description: |
  **PDF Data Extraction Pipeline**: Extracts complete structured data from any PDF book or document — page images, word-level text with bounding boxes, embedded images, auto-detected structure (chapters/sections), search index, and full text — then packages everything into a ready-to-use ZIP. Use this skill whenever the user wants to: analyze a PDF's content structure, extract all data from a PDF for reuse in another system, prepare a PDF for building an interactive web viewer, create a data layer from a PDF book or textbook, or convert a PDF into structured machine-readable data. Trigger on phrases like "extract data from PDF", "analyze this book", "prepare PDF for web", "build data package from PDF", "pipeline this PDF", or any request to systematically pull apart a PDF into reusable components.
---

# PDF Data Extraction Pipeline

This skill turns any PDF into a complete, structured data package ready to plug into a web viewer or any other system.

## What It Produces

Given a PDF, this pipeline outputs a ZIP containing:

```
book-data/
├── manifest.json          # Master metadata + schema version
├── book-structure.json    # Auto-detected chapters/sections/topics
├── search-index.json      # Every word with page + position for search
├── full-text.txt          # Complete text organized by section
├── pages/
│   ├── page-001.webp      # High-res page renders (200 DPI)
│   ├── page-002.webp
│   └── ...
├── words/
│   ├── page-001.json      # Word-level bounding boxes per page
│   ├── page-002.json
│   └── ...
└── images/
    ├── page-001-img-01.jpg # Extracted embedded images
    ├── page-003-img-01.png
    └── ...
```

## How to Run the Pipeline

The extraction is handled by the bundled Python script. Run it like this:

```bash
python3 SKILL_DIR/scripts/extract_pdf.py INPUT_PDF OUTPUT_DIR [OPTIONS]
```

Where:
- `SKILL_DIR` is this skill's directory (where this SKILL.md lives)
- `INPUT_PDF` is the path to the PDF file
- `OUTPUT_DIR` is where the `book-data/` folder and final ZIP will be created

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--dpi` | 200 | Resolution for page image rendering |
| `--format` | webp | Image format: webp, png, or jpg |
| `--no-images` | false | Skip embedded image extraction |
| `--no-zip` | false | Don't create the final ZIP (just the folder) |
| `--title` | auto | Override the detected book title |
| `--author` | auto | Override the detected author |
| `--lang` | auto | Override language detection |

### Example

```bash
# Full extraction with defaults
python3 /path/to/pdf-extract/scripts/extract_pdf.py \
    /path/to/my-book.pdf \
    /path/to/output/

# Custom DPI and PNG format
python3 /path/to/pdf-extract/scripts/extract_pdf.py \
    /path/to/my-book.pdf \
    /path/to/output/ \
    --dpi 300 --format png
```

## Step-by-Step Workflow

When a user asks you to extract data from a PDF:

1. **Locate the PDF** — check uploads or ask the user for the path
2. **Run the script** — execute `extract_pdf.py` with appropriate options
3. **Report results** — tell the user what was extracted (page count, word count, image count, detected structure)
4. **Deliver the ZIP** — save it to the outputs folder with a computer:// link

The script handles everything automatically: page rendering, text extraction with bounding boxes, embedded image extraction, structure detection, search index building, and ZIP packaging. It prints progress as it goes.

## Output Format Details

### manifest.json
The manifest is the entry point for any system consuming this data. It contains:
- `schema_version`: Always "1.0" for this format
- `source_pdf`: Original filename
- `title`, `author`, `language`: Auto-detected or overridden
- `total_pages`, `total_words`, `total_images`: Counts
- `created_at`: ISO timestamp
- `dpi`, `image_format`: Rendering settings used
- `files`: Inventory of all generated files

### words/page-NNN.json
Each word file contains every word on that page with percentage-based coordinates:
```json
{
  "page": 5,
  "width": 595.32,
  "height": 841.92,
  "words": [
    {"t": "Hello", "x0": 10.5, "y0": 20.3, "x1": 25.1, "y1": 23.8}
  ],
  "images": [
    {"x0": 5.0, "y0": 50.0, "x1": 95.0, "y1": 80.0}
  ],
  "full_text": "Hello world..."
}
```
Coordinates are percentages of page dimensions (0-100), making them resolution-independent.

### book-structure.json
Auto-detected structure based on text analysis (font sizes, positions, keywords):
```json
{
  "title": "Detected Book Title",
  "author": "Detected Author",
  "sections": [...],
  "chapters": [...],
  "page_metadata": [...]
}
```

### search-index.json
Flat array of searchable entries, one per unique word occurrence:
```json
[
  {"word": "hello", "page": 5, "x0": 10.5, "y0": 20.3, "x1": 25.1, "y1": 23.8}
]
```

## Dependencies

The script requires these Python packages and system tools:
- `pdfplumber` (pip)
- `pdf2image` (pip)
- `Pillow` (pip)
- `poppler-utils` (system — provides `pdfimages` and `pdftoppm`)

Install if missing:
```bash
pip install pdfplumber pdf2image Pillow --break-system-packages
apt-get install -y poppler-utils  # if not already installed
```

## Handling Edge Cases

- **Scanned PDFs** (no extractable text): The script detects this and warns the user. Word extraction will be empty but page images and embedded images still work. Suggest OCR as a follow-up.
- **Very large PDFs** (500+ pages): The script processes pages in batches to manage memory. It may take several minutes.
- **Password-protected PDFs**: The script will fail with a clear error message. Ask the user for the password.
- **Non-book PDFs** (forms, slides, reports): Structure detection adapts — it won't force a "chapter" hierarchy on a slide deck. It classifies pages by type instead.
