// Converts snake_case and camelCase keys into clean Title Case labels
function formatKeyLabel(key) {
  return key
    .replace(/_/g, ' ')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/\b\w/g, c => c.toUpperCase());
}

function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// Renders data into human-friendly cards, labels, and tags
function renderHumanView(data) {
  if (data === null || data === undefined) {
    return '<span class="empty-table-msg">N/A</span>';
  }
  if (typeof data === 'boolean') {
    return `<span class="badge ${data ? 'badge-approved' : 'badge-rejected'}">${data}</span>`;
  }
  if (typeof data === 'number' || typeof data === 'string') {
    return `<span>${escapeHtml(data)}</span>`;
  }
  if (Array.isArray(data)) {
    if (data.length === 0) return '<span class="empty-table-msg">None</span>';
    const isPrimitiveArray = data.every(item => typeof item !== 'object' || item === null);
    if (isPrimitiveArray) {
      return '<div class="chip-container">' +
        data.map(item => `<span class="chip">${escapeHtml(item)}</span>`).join('') +
        '</div>';
    } else {
      return '<div>' +
        data.map((item, idx) => `<div class="nested-card"><span class="human-label">Item #${idx + 1}</span>${renderHumanView(item)}</div>`).join('') +
        '</div>';
    }
  }
  if (typeof data === 'object') {
    const keys = Object.keys(data);
    if (keys.length === 0) return '<span class="empty-table-msg">No details</span>';
    let html = '<div class="human-grid">';
    for (const key of keys) {
      const label = formatKeyLabel(key);
      html += `<div class="human-field">
                <span class="human-label">${escapeHtml(label)}</span>
                <div class="human-value">${renderHumanView(data[key])}</div>
            </div>`;
    }
    html += '</div>';
    return html;
  }
  return `<span>${escapeHtml(data)}</span>`;
}

// Generates human-friendly form inputs with labels
function renderEditFields(data, path = '') {
  if (data === null || data === undefined) data = '';

  if (typeof data === 'boolean') {
    const label = formatKeyLabel(path.split('.').pop());
    return `<div class="form-group">
            <label class="human-label">${escapeHtml(label)}</label>
            <select class="form-input edit-input" data-path="${path}" data-type="boolean">
                <option value="true" ${data ? 'selected' : ''}>True</option>
                <option value="false" ${!data ? 'selected' : ''}>False</option>
            </select>
        </div>`;
  }

  if (typeof data === 'number' || typeof data === 'string') {
    const label = formatKeyLabel(path.split('.').pop());
    const strVal = String(data);
    const isLong = strVal.length > 60 || strVal.includes('\n');
    if (isLong) {
      return `<div class="form-group">
                <label class="human-label">${escapeHtml(label)}</label>
                <textarea class="form-input edit-input" data-path="${path}" data-type="${typeof data}" rows="3">${escapeHtml(strVal)}</textarea>
            </div>`;
    } else {
      return `<div class="form-group">
                <label class="human-label">${escapeHtml(label)}</label>
                <input type="text" class="form-input edit-input" data-path="${path}" data-type="${typeof data}" value="${escapeHtml(strVal)}">
            </div>`;
    }
  }

  if (Array.isArray(data)) {
    const label = formatKeyLabel(path.split('.').pop());
    const isPrimitiveArray = data.every(item => typeof item !== 'object' || item === null);
    if (isPrimitiveArray) {
      const joinedVal = data.join(', ');
      return `<div class="form-group">
                <label class="human-label">${escapeHtml(label)} <span style="font-weight:normal; text-transform:none; opacity: 0.7;">(Separated by commas)</span></label>
                <input type="text" class="form-input edit-input" data-path="${path}" data-type="array-primitive" value="${escapeHtml(joinedVal)}">
            </div>`;
    } else {
      let html = `<fieldset class="nested-fieldset"><legend class="human-label">${escapeHtml(label)} List</legend>`;
      data.forEach((item, idx) => {
        html += `<div class="nested-card">
                    <span class="human-label" style="opacity: 0.8;">Item #${idx + 1}</span>
                    ${renderEditFields(item, path ? `${path}[${idx}]` : `[${idx}]`)}
                </div>`;
      });
      html += `</fieldset>`;
      return html;
    }
  }

  if (typeof data === 'object') {
    const keyName = path ? path.split('.').pop() : '';
    const label = keyName ? formatKeyLabel(keyName) : '';
    let html = '';
    if (label) {
      html += `<fieldset class="nested-fieldset"><legend class="human-label">${escapeHtml(label)}</legend>`;
    }
    for (const [key, val] of Object.entries(data)) {
      const currentPath = path ? `${path}.${key}` : key;
      html += renderEditFields(val, currentPath);
    }
    if (label) {
      html += `</fieldset>`;
    }
    return html;
  }
  return '';
}

// Toggles edit form display
function toggleEdit(table, index) {
  const editSec = document.getElementById(`edit-${table}-${index}`);
  if (editSec.style.display === 'block') {
    editSec.style.display = 'none';
  } else {
    editSec.style.display = 'block';
  }
}

// Toggles technical raw JSON view
function toggleRawView(table, index) {
  const rawSec = document.getElementById(`raw-view-${table}-${index}`);
  rawSec.style.display = (rawSec.style.display === 'block') ? 'none' : 'block';
}

// Reconstructs form inputs back into structured JSON before submission
function prepareEditSubmission(event, table, index) {
  event.preventDefault();
  const form = event.target;
  const container = document.getElementById(`edit-fields-${table}-${index}`);
  const inputs = container.querySelectorAll('.edit-input');

  const rawJsonElem = document.getElementById(`json-data-${table}-${index}`);
  let obj;
  try {
    obj = JSON.parse(rawJsonElem.textContent);
  } catch (e) {
    obj = {};
  }

  inputs.forEach(input => {
    const path = input.getAttribute('data-path');
    const dataType = input.getAttribute('data-type');
    let val = input.value;

    if (dataType === 'number') {
      val = Number(val);
    } else if (dataType === 'boolean') {
      val = (val === 'true');
    } else if (dataType === 'array-primitive') {
      val = val.split(',').map(s => s.trim()).filter(s => s.length > 0);
    }

    setNestedValue(obj, path, val);
  });

  document.getElementById(`edited-data-${table}-${index}`).value = JSON.stringify(obj);
  form.submit();
}

function setNestedValue(obj, path, value) {
  const parts = path.replace(/\[(\d+)\]/g, '.$1').split('.');
  let current = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    const part = parts[i];
    if (!(part in current)) {
      current[part] = {};
    }
    current = current[part];
  }
  current[parts[parts.length - 1]] = value;
}

// Render all records on page load
document.addEventListener("DOMContentLoaded", function () {
  const rawScripts = document.querySelectorAll("script[id^='json-data-']");
  rawScripts.forEach(script => {
    const idParts = script.id.replace("json-data-", "");
    const viewContainer = document.getElementById(`view-${idParts}`);
    const editFieldsContainer = document.getElementById(`edit-fields-${idParts}`);

    try {
      const data = JSON.parse(script.textContent);
      if (viewContainer) {
        viewContainer.innerHTML = renderHumanView(data);
      }
      if (editFieldsContainer) {
        editFieldsContainer.innerHTML = renderEditFields(data);
      }
    } catch (e) {
      if (viewContainer) {
        viewContainer.innerHTML = `<span style="color:#f87171;">Unable to display record data.</span>`;
      }
    }
  });
});
