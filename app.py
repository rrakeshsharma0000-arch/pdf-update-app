import os
import io
import re
import uuid
import fitz  # PyMuPDF
from flask import Flask, request, jsonify, send_file, render_template

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

UPLOAD_FOLDER = '/tmp/pdf_uploads'
FONT_FOLDER   = '/tmp/pdf_fonts'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(FONT_FOLDER, exist_ok=True)

# The 14 core PDF fonts — PyMuPDF has built-in substitutes for these
_CORE_FONT_PATTERNS = ('helvetica', 'times', 'courier', 'symbol', 'zapf')

def _normalize_font_name(raw: str) -> str:
    """Strip PDF subset prefix (e.g. 'ABCDEF+') and whitespace."""
    return re.sub(r'^[A-Z]{6}\+', '', raw).strip()

def _is_subset_font(raw: str) -> bool:
    """Subset fonts have a 6-uppercase-letter prefix. They only contain glyphs
    used in the original document, so replacement text with new characters will
    show missing glyphs. Never use subset fonts for re-insertion."""
    return bool(re.match(r'^[A-Z]{6}\+', raw.strip()))

def _is_core_font(name: str) -> bool:
    low = name.lower()
    return any(p in low for p in _CORE_FONT_PATTERNS)

def _safe_font_key(name: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_\-]', '_', name)

def _font_file_path(font_name: str):
    """Return path to a stored font file for this name, or None."""
    key = _safe_font_key(font_name)
    for ext in ('.ttf', '.otf', '.TTF', '.OTF'):
        p = os.path.join(FONT_FOLDER, key + ext)
        if os.path.exists(p):
            return p
    # Fuzzy: stem contained in name or vice-versa
    low = font_name.lower()
    for fname in os.listdir(FONT_FOLDER):
        stem = os.path.splitext(fname)[0].lower()
        if stem and (stem in low or low in stem):
            return os.path.join(FONT_FOLDER, fname)
    return None

def _builtin_font(flags: int, font_name: str) -> str:
    name = font_name.lower()
    is_bold   = bool(flags & (1 << 4)) or 'bold'    in name
    is_italic = bool(flags & (1 << 1)) or 'italic'  in name or 'oblique' in name
    is_mono   = bool(flags & (1 << 3)) or any(x in name for x in ('courier', 'mono', 'consol', 'code'))
    is_serif  = bool(flags & (1 << 2)) or any(x in name for x in ('times', 'georgia', 'garamond', 'palatino'))
    if is_mono:
        if is_bold and is_italic: return 'cobi'
        if is_bold:               return 'cobo'
        if is_italic:             return 'coit'
        return 'cour'
    if is_serif:
        if is_bold and is_italic: return 'tibi'
        if is_bold:               return 'tibo'
        if is_italic:             return 'tiit'
        return 'tiro'
    if is_bold and is_italic: return 'hebi'
    if is_bold:               return 'hebo'
    if is_italic:             return 'heit'
    return 'helv'

def _color_to_rgb(color_int: int):
    r = ((color_int >> 16) & 0xFF) / 255.0
    g = ((color_int >>  8) & 0xFF) / 255.0
    b = ( color_int        & 0xFF) / 255.0
    return (r, g, b)

def _span_style_at(page, rect: fitz.Rect):
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if fitz.Rect(span["bbox"]).intersects(rect):
                    return span
    return None

def _search_all(page, text: str, case_sensitive: bool):
    hits = page.search_for(text)
    if hits or case_sensitive:
        return hits
    pattern = re.compile(re.escape(text), re.IGNORECASE)
    rects = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                stext = span["text"]
                if not pattern.search(stext):
                    continue
                bbox = span["bbox"]
                char_w = (bbox[2] - bbox[0]) / max(len(stext), 1)
                for m in pattern.finditer(stext):
                    x0 = bbox[0] + m.start() * char_w
                    x1 = bbox[0] + m.end()   * char_w
                    rects.append(fitz.Rect(x0, bbox[1], x1, bbox[3]))
    return rects


def extract_and_store_fonts(pdf_bytes: bytes):
    """
    For every non-core font embedded in the PDF, extract its binary data
    and save it to FONT_FOLDER so it can be used during re-insertion.
    Returns a list of dicts: {name, auto_extracted, uploaded}.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    seen = set()
    font_report = []

    for page in doc:
        for font in page.get_fonts(full=True):
            xref, ext, ftype, basefont, fname, enc, _ = font
            raw_name = basefont or fname or ''
            clean = _normalize_font_name(raw_name)
            if not clean or clean in seen:
                continue
            seen.add(clean)

            # Skip the 14 core fonts — PyMuPDF handles them natively
            if _is_core_font(clean):
                continue

            # Subset fonts (ABCDEF+ prefix) only contain glyphs from the original
            # document. Replacement text may use different characters not in the
            # subset, causing them to render as blank. Mark these clearly.
            is_subset = _is_subset_font(basefont or fname or '')

            # Check if already stored
            if _font_file_path(clean):
                font_report.append({
                    'name': clean,
                    'auto_extracted': True,
                    'uploaded': True,
                    'is_subset': is_subset,
                })
                continue

            # Try to auto-extract the font bytes from the PDF
            auto_extracted = False
            if xref > 0 and not is_subset:
                # Only extract full (non-subset) fonts — subsets are incomplete
                try:
                    font_data = doc.extract_font(xref)
                    content = font_data[3] if len(font_data) > 3 else None
                    file_ext = (font_data[1] or 'ttf').lstrip('.')
                    if file_ext not in ('ttf', 'otf', 'cff'):
                        file_ext = 'ttf'
                    if content and len(content) > 256:
                        dest = os.path.join(FONT_FOLDER, _safe_font_key(clean) + '.' + file_ext)
                        with open(dest, 'wb') as f:
                            f.write(content)
                        auto_extracted = True
                except Exception:
                    pass

            font_report.append({
                'name': clean,
                'auto_extracted': auto_extracted,
                'uploaded': auto_extracted,
                'is_subset': is_subset,
            })

    doc.close()
    return font_report


def extract_preview_text(pdf_bytes: bytes) -> str:
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    parts = []
    for i, page in enumerate(doc):
        text = page.get_text()
        if text.strip():
            parts.append(f"=== Page {i + 1} ===\n{text.strip()}")
    doc.close()
    return "\n\n".join(parts)


def apply_replacements_inplace(pdf_bytes: bytes, replacements: list, case_sensitive: bool):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total_changes = 0
    results = []

    for item in replacements:
        find_text    = item.get('find', '').strip()
        replace_text = item.get('replace', '').strip()
        if not find_text:
            continue

        count = 0
        for page in doc:
            hits = _search_all(page, find_text, case_sensitive)
            if not hits:
                continue

            styles = [_span_style_at(page, rect) for rect in hits]

            # Resolve font info before redacting (span data is still valid)
            inserts = []
            for rect, span in zip(hits, styles):
                if span is None:
                    fontname = 'helv'
                    fontsize = 11
                    color    = (0, 0, 0)
                    baseline_y = rect.y1 - rect.height * 0.15
                else:
                    raw_name = span['font']
                    color    = _color_to_rgb(span['color'])
                    fontsize = span['size']
                    # rect.y1 is bottom of bounding box; baseline sits just above it
                    baseline_y = rect.y1 - (rect.height * 0.15)

                    font_file = None
                    if not _is_subset_font(raw_name):
                        font_file = _font_file_path(_normalize_font_name(raw_name))

                    if font_file:
                        clean_key = re.sub(r'[^a-zA-Z0-9]', '', _normalize_font_name(raw_name))[:32] or 'F1'
                        page.insert_font(fontname=clean_key, fontfile=font_file)
                        fontname = clean_key
                    else:
                        fontname = _builtin_font(span['flags'], raw_name)

                # White out the original text
                page.add_redact_annot(rect, fill=(1, 1, 1))
                inserts.append((fitz.Point(rect.x0, baseline_y), fontname, fontsize, color))
                count += 1

            # Apply redactions first so the white fill is in the stream,
            # then insert text on top — this preserves the exact font size.
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)

            for origin, fontname, fontsize, color in inserts:
                page.insert_text(
                    origin,
                    replace_text,
                    fontname=fontname,
                    fontsize=fontsize,
                    color=color,
                )

        results.append({'find': find_text, 'replace': replace_text, 'count': count})
        total_changes += count

    buf = io.BytesIO()
    doc.save(buf, garbage=4, deflate=True)
    doc.close()
    buf.seek(0)
    return buf.read(), total_changes, results


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/preview', methods=['POST'])
def preview_pdf():
    if 'pdf' not in request.files:
        return jsonify({'error': 'No PDF file uploaded'}), 400

    pdf_file = request.files['pdf']
    if not pdf_file.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'File must be a PDF'}), 400

    pdf_bytes = pdf_file.read()
    try:
        text       = extract_preview_text(pdf_bytes)
        font_report = extract_and_store_fonts(pdf_bytes)
    except Exception as e:
        return jsonify({'error': f'Could not read PDF: {str(e)}'}), 400

    if not text.strip():
        return jsonify({'error': 'PDF appears to be empty or contains no extractable text (scanned images are not supported)'}), 400

    file_id = str(uuid.uuid4())
    with open(os.path.join(UPLOAD_FOLDER, f'{file_id}.raw.pdf'), 'wb') as f:
        f.write(pdf_bytes)

    # Subset fonts need the user to supply the full font file for exact matching.
    # Non-subset fonts were auto-extracted and are ready to use.
    missing = [f for f in font_report if not f['uploaded']]  # couldn't extract at all
    subset_fonts = [f for f in font_report if f.get('is_subset')]  # embedded subsets
    auto_ok = [f for f in font_report if f['auto_extracted']]

    # Merge: subset fonts also need manual upload (full version)
    needs_upload = {f['name'] for f in missing} | {f['name'] for f in subset_fonts}
    custom_fonts_for_ui = [
        {'name': n, 'uploaded': _font_file_path(n) is not None}
        for n in needs_upload
    ]

    return jsonify({
        'file_id': file_id,
        'text': text,
        'fonts_auto_extracted': len(auto_ok),
        'custom_fonts': custom_fonts_for_ui,
    })


@app.route('/fonts/upload', methods=['POST'])
def upload_font():
    font_name = request.form.get('font_name', '').strip()
    if not font_name:
        return jsonify({'error': 'font_name is required'}), 400
    if 'font_file' not in request.files:
        return jsonify({'error': 'No font file provided'}), 400

    font_file = request.files['font_file']
    ext = os.path.splitext(font_file.filename)[1].lower()
    if ext not in ('.ttf', '.otf'):
        return jsonify({'error': 'Only .ttf and .otf files are supported'}), 400

    dest = os.path.join(FONT_FOLDER, _safe_font_key(font_name) + ext)
    font_file.save(dest)
    return jsonify({'ok': True})


@app.route('/raw/<file_id>')
def serve_raw_pdf(file_id):
    if not file_id.replace('-', '').isalnum():
        return jsonify({'error': 'Invalid file ID'}), 400
    file_path = os.path.join(UPLOAD_FOLDER, f'{file_id}.raw.pdf')
    if not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404
    return send_file(file_path, mimetype='application/pdf')


@app.route('/process', methods=['POST'])
def process_pdf():
    data = request.get_json()
    if not data:
        return jsonify({'error': 'Invalid request'}), 400

    file_id        = data.get('file_id', '').strip()
    replacements   = data.get('replacements', [])
    case_sensitive = data.get('case_sensitive', False)

    if not file_id or not file_id.replace('-', '').isalnum():
        return jsonify({'error': 'Invalid file ID'}), 400
    if not replacements:
        return jsonify({'error': 'No replacements provided'}), 400

    raw_path = os.path.join(UPLOAD_FOLDER, f'{file_id}.raw.pdf')
    if not os.path.exists(raw_path):
        return jsonify({'error': 'PDF not found. Please re-upload the file.'}), 404

    with open(raw_path, 'rb') as f:
        pdf_bytes = f.read()

    try:
        updated_pdf_bytes, total_changes, results = apply_replacements_inplace(
            pdf_bytes, replacements, case_sensitive
        )
    except Exception as e:
        return jsonify({'error': f'PDF processing failed: {str(e)}'}), 500

    if total_changes == 0:
        return jsonify({'error': 'No matches found. Check your search terms and try again.'}), 400

    output_id = str(uuid.uuid4())
    with open(os.path.join(UPLOAD_FOLDER, f'{output_id}.pdf'), 'wb') as f:
        f.write(updated_pdf_bytes)

    return jsonify({'file_id': output_id, 'total_changes': total_changes, 'results': results})


@app.route('/download/<file_id>')
def download_pdf(file_id):
    if not file_id.replace('-', '').isalnum():
        return jsonify({'error': 'Invalid file ID'}), 400
    file_path = os.path.join(UPLOAD_FOLDER, f'{file_id}.pdf')
    if not os.path.exists(file_path):
        return jsonify({'error': 'File not found'}), 404
    return send_file(file_path, as_attachment=True, download_name='updated_document.pdf', mimetype='application/pdf')


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)
