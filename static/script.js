// --- State ---
let currentFileId = null;
let currentFilename = '';

// --- Element refs ---
const dropZone     = document.getElementById('dropZone');
const pdfInput     = document.getElementById('pdfInput');
const browseBtn    = document.getElementById('browseBtn');
const fileInfo     = document.getElementById('fileInfo');
const uploadBtn    = document.getElementById('uploadBtn');
const uploadBtnText = document.getElementById('uploadBtnText');
const uploadSpinner = document.getElementById('uploadSpinner');

const stepUpload   = document.getElementById('stepUpload');
const stepReplace  = document.getElementById('stepReplace');
const previewCard       = document.getElementById('previewCard');
const previewFrame      = document.getElementById('previewFrame');
const fontWarningCard   = document.getElementById('fontWarningCard');
const fontList          = document.getElementById('fontList');

const previewFilename = document.getElementById('previewFilename');
const changeFileBtn   = document.getElementById('changeFileBtn');
const pairsList       = document.getElementById('pairsList');
const addPairBtn      = document.getElementById('addPairBtn');
const caseSensitive   = document.getElementById('caseSensitive');
const applyBtn        = document.getElementById('applyBtn');
const applyBtnText    = document.getElementById('applyBtnText');
const applySpinner    = document.getElementById('applySpinner');

const resultBox    = document.getElementById('resultBox');
const resultMsg    = document.getElementById('resultMsg');
const downloadLink = document.getElementById('downloadLink');
const startOverBtn = document.getElementById('startOverBtn');
const errorBox     = document.getElementById('errorBox');
const errorMsg     = document.getElementById('errorMsg');

// --- File selection ---
let selectedFile = null;

function setFile(file) {
  if (!file || !file.name.toLowerCase().endsWith('.pdf')) {
    showError('Please select a valid PDF file.');
    return;
  }
  selectedFile = file;
  fileInfo.textContent = `${file.name}  (${(file.size / 1024).toFixed(1)} KB)`;
  fileInfo.classList.remove('hidden');
  dropZone.classList.add('has-file');
  uploadBtn.disabled = false;
  hideError();
}

browseBtn.addEventListener('click', () => pdfInput.click());
dropZone.addEventListener('click', (e) => { if (e.target !== browseBtn) pdfInput.click(); });
pdfInput.addEventListener('change', () => { if (pdfInput.files[0]) setFile(pdfInput.files[0]); });
dropZone.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
dropZone.addEventListener('drop', (e) => {
  e.preventDefault();
  dropZone.classList.remove('dragover');
  if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]);
});

// --- Upload & preview ---
uploadBtn.addEventListener('click', async () => {
  if (!selectedFile) return;
  setUploadLoading(true);
  hideError();

  const formData = new FormData();
  formData.append('pdf', selectedFile);

  try {
    const res = await fetch('/preview', { method: 'POST', body: formData });
    const data = await res.json();
    if (!res.ok) { showError(data.error || 'Upload failed.'); return; }

    currentFileId = data.file_id;
    currentFilename = selectedFile.name;
    previewFilename.textContent = currentFilename;

    // #navpanes=0 collapses the browser's page-thumbnail sidebar
    previewFrame.src = `/raw/${data.file_id}#navpanes=0&toolbar=1`;

    // Show font info — auto-extracted fonts need no action; missing ones need upload
    renderFontWarning(data.custom_fonts || [], data.fonts_auto_extracted || 0);

    stepUpload.classList.add('hidden');
    previewCard.classList.remove('hidden');
    stepReplace.classList.remove('hidden');
    resultBox.classList.add('hidden');

    // Reset pairs to one empty row
    pairsList.innerHTML = '';
    addPair();
  } catch {
    showError('Network error. Please try again.');
  } finally {
    setUploadLoading(false);
  }
});

function setUploadLoading(on) {
  uploadBtn.disabled = on;
  uploadBtnText.textContent = on ? 'Uploading…' : 'Upload & Preview';
  uploadSpinner.classList.toggle('hidden', !on);
}

// --- Replacement pairs ---
function addPair(find = '', replace = '') {
  const row = document.createElement('div');
  row.className = 'pair-row';

  // Add column headers only for first pair
  if (pairsList.children.length === 0) {
    const labels = document.createElement('div');
    labels.className = 'pair-labels';
    labels.innerHTML = '<span>Find</span><span>Replace with</span><span></span>';
    pairsList.appendChild(labels);
  }

  const findInput = document.createElement('input');
  findInput.type = 'text';
  findInput.placeholder = 'Text to find…';
  findInput.value = find;

  const replaceInput = document.createElement('input');
  replaceInput.type = 'text';
  replaceInput.placeholder = 'Replace with…';
  replaceInput.value = replace;

  const removeBtn = document.createElement('button');
  removeBtn.type = 'button';
  removeBtn.className = 'remove-btn';
  removeBtn.title = 'Remove';
  removeBtn.textContent = '×';
  removeBtn.addEventListener('click', () => {
    row.remove();
    // Remove labels if no pairs left
    if (pairsList.querySelectorAll('.pair-row').length === 0) {
      pairsList.innerHTML = '';
    }
  });

  row.appendChild(findInput);
  row.appendChild(replaceInput);
  row.appendChild(removeBtn);
  pairsList.appendChild(row);
  findInput.focus();
}

addPairBtn.addEventListener('click', () => addPair());

// --- Apply replacements ---
applyBtn.addEventListener('click', async () => {
  const rows = pairsList.querySelectorAll('.pair-row');
  const replacements = [];
  rows.forEach(row => {
    const inputs = row.querySelectorAll('input');
    const find = inputs[0].value.trim();
    const replace = inputs[1].value.trim();
    if (find) replacements.push({ find, replace });
  });

  if (replacements.length === 0) {
    showError('Please enter at least one search term.');
    return;
  }

  setApplyLoading(true);
  hideError();

  try {
    const res = await fetch('/process', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        file_id: currentFileId,
        replacements,
        case_sensitive: caseSensitive.checked,
      }),
    });
    const data = await res.json();
    if (!res.ok) { showError(data.error || 'Processing failed.'); return; }

    const summary = data.results
      .filter(r => r.count > 0)
      .map(r => `"${r.find}" → "${r.replace}" (${r.count} replacement${r.count !== 1 ? 's' : ''})`)
      .join(' · ');

    resultMsg.textContent = `${data.total_changes} change${data.total_changes !== 1 ? 's' : ''} made. ${summary}`;
    downloadLink.href = `/download/${data.file_id}`;
    stepReplace.classList.add('hidden');
    resultBox.classList.remove('hidden');
  } catch {
    showError('Network error. Please try again.');
  } finally {
    setApplyLoading(false);
  }
});

function setApplyLoading(on) {
  applyBtn.disabled = on;
  applyBtnText.textContent = on ? 'Generating PDF…' : 'Apply & Generate PDF';
  applySpinner.classList.toggle('hidden', !on);
}

// --- Navigation ---
function renderFontWarning(customFonts, autoExtracted) {
  const icon  = document.getElementById('fontWarningIcon');
  const title = document.getElementById('fontWarningTitle');
  const desc  = document.getElementById('fontWarningDesc');

  if (!customFonts.length && !autoExtracted) {
    fontWarningCard.classList.add('hidden');
    return;
  }

  if (!customFonts.length && autoExtracted) {
    // All custom fonts extracted automatically — green success state
    icon.textContent  = '✅';
    title.textContent = 'Fonts Ready';
    desc.textContent  = `${autoExtracted} custom font${autoExtracted !== 1 ? 's' : ''} were automatically extracted from the PDF. Replaced text will match the original font exactly.`;
    fontList.innerHTML = '';
    fontWarningCard.classList.remove('hidden');
    return;
  }

  // Some fonts couldn't be extracted — show upload option
  icon.textContent  = '⚠️';
  title.textContent = 'Some Fonts Need Uploading';
  const autoMsg = autoExtracted ? ` ${autoExtracted} font${autoExtracted !== 1 ? 's were' : ' was'} auto-extracted.` : '';
  desc.textContent  = `${customFonts.length} font${customFonts.length !== 1 ? 's' : ''} in this PDF are embedded as subsets (only the glyphs used in the original are included). Replaced text may use different characters that are missing from the subset, causing blank characters. Upload the full .ttf/.otf font file below to fix this.${autoMsg}`;

  fontList.innerHTML = '';
  customFonts.forEach(f => {
    const row = document.createElement('div');
    row.className = 'font-row';
    row.dataset.fontName = f.name;

    const nameEl = document.createElement('span');
    nameEl.className = 'font-row-name';
    nameEl.title = f.name;
    nameEl.textContent = f.name;

    const badge = document.createElement('span');
    badge.className = `font-badge ${f.uploaded ? 'uploaded' : 'missing'}`;
    badge.textContent = f.uploaded ? '✓ Uploaded' : 'Missing';

    const fileInput = document.createElement('input');
    fileInput.type = 'file';
    fileInput.accept = '.ttf,.otf';
    fileInput.className = 'font-upload-input';

    const uploadBtn = document.createElement('button');
    uploadBtn.className = 'font-upload-btn';
    uploadBtn.textContent = f.uploaded ? 'Replace font' : 'Upload font';

    uploadBtn.addEventListener('click', () => fileInput.click());
    fileInput.addEventListener('change', async () => {
      const file = fileInput.files[0];
      if (!file) return;
      uploadBtn.textContent = 'Uploading…';
      uploadBtn.disabled = true;

      const fd = new FormData();
      fd.append('font_name', f.name);
      fd.append('font_file', file);

      try {
        const res = await fetch('/fonts/upload', { method: 'POST', body: fd });
        const data = await res.json();
        if (res.ok) {
          badge.className = 'font-badge uploaded';
          badge.textContent = '✓ Uploaded';
          uploadBtn.textContent = 'Replace font';
        } else {
          badge.textContent = data.error || 'Upload failed';
        }
      } catch {
        badge.textContent = 'Upload failed';
      } finally {
        uploadBtn.disabled = false;
        fileInput.value = '';
      }
    });

    row.appendChild(nameEl);
    row.appendChild(badge);
    row.appendChild(uploadBtn);
    row.appendChild(fileInput);
    fontList.appendChild(row);
  });

  fontWarningCard.classList.remove('hidden');
}

changeFileBtn.addEventListener('click', () => {
  stepReplace.classList.add('hidden');
  previewCard.classList.add('hidden');
  fontWarningCard.classList.add('hidden');
  stepUpload.classList.remove('hidden');
  resetUploadArea();
});

startOverBtn.addEventListener('click', () => {
  resultBox.classList.add('hidden');
  previewCard.classList.add('hidden');
  fontWarningCard.classList.add('hidden');
  stepReplace.classList.add('hidden');
  stepUpload.classList.remove('hidden');
  resetUploadArea();
});

function resetUploadArea() {
  selectedFile = null;
  currentFileId = null;
  fileInfo.classList.add('hidden');
  dropZone.classList.remove('has-file');
  uploadBtn.disabled = true;
  pdfInput.value = '';
  hideError();
}

// --- Error helpers ---
function showError(msg) {
  errorMsg.textContent = msg;
  errorBox.classList.remove('hidden');
}

function hideError() {
  errorBox.classList.add('hidden');
}
