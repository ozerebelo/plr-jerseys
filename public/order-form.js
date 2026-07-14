function openLightbox(src) {
  if (!src) return;
  document.getElementById('lightbox-img').src = src;
  document.getElementById('lightbox').style.display = 'flex';
}

function closeLightbox() {
  document.getElementById('lightbox').style.display = 'none';
  document.getElementById('lightbox-img').src = '';
}

document.addEventListener('keydown', function (e) {
  if (e.key === 'Escape') closeLightbox();
});

function populateSeasonSelect(selectEl, values) {
  selectEl.innerHTML = '';
  const placeholder = document.createElement('option');
  placeholder.value = '';
  // A disabled+selected option is excluded from form submission entirely, which
  // shifts every later line item's array index out of alignment with the other
  // fields. Only disable it when there are real choices to force a pick from;
  // the "no options" case must stay submittable as an empty value.
  placeholder.disabled = values.length > 0;
  placeholder.selected = true;
  placeholder.textContent = values.length ? 'Escolhe a temporada' : 'Sem temporadas indicadas';
  selectEl.appendChild(placeholder);
  values.forEach(function (v) {
    const opt = document.createElement('option');
    opt.value = v;
    opt.textContent = v;
    selectEl.appendChild(opt);
  });
  const customOpt = document.createElement('option');
  customOpt.value = '__custom__';
  customOpt.textContent = '+ Outra (não listada)';
  selectEl.appendChild(customOpt);
}

function toggleSeasonCustom(selectEl) {
  const row = selectEl.closest('.line-item');
  const flag = row.querySelector('.season-custom-flag');
  const input = row.querySelector('.season-custom-input');
  if (selectEl.value === '__custom__') {
    selectEl.disabled = true;
    selectEl.style.display = 'none';
    flag.value = '1';
    input.disabled = false;
    input.style.display = 'block';
    input.required = true;
    input.focus();
  } else {
    flag.value = '';
    input.disabled = true;
    input.style.display = 'none';
    input.required = false;
    input.value = '';
  }
}

function populateKitSelect(selectEl, values) {
  selectEl.innerHTML = '';
  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.disabled = values.length > 0;
  placeholder.selected = true;
  placeholder.textContent = values.length ? 'Escolhe o tipo' : 'Sem tipos indicados';
  selectEl.appendChild(placeholder);
  values.forEach(function (v) {
    const opt = document.createElement('option');
    opt.value = v;
    opt.textContent = v;
    selectEl.appendChild(opt);
  });
  const customOpt = document.createElement('option');
  customOpt.value = '__custom__';
  customOpt.textContent = '+ Outro (não listado)';
  selectEl.appendChild(customOpt);
}

function toggleKitCustom(selectEl) {
  const row = selectEl.closest('.line-item');
  const flag = row.querySelector('.kit-custom-flag');
  const input = row.querySelector('.kit-custom-input');
  if (selectEl.value === '__custom__') {
    selectEl.disabled = true;
    selectEl.style.display = 'none';
    flag.value = '1';
    input.disabled = false;
    input.style.display = 'block';
    input.required = true;
    input.focus();
  } else {
    flag.value = '';
    input.disabled = true;
    input.style.display = 'none';
    input.required = false;
    input.value = '';
  }
}

function updateItemFields(selectEl) {
  const row = selectEl.closest('.line-item');
  const seasonSelect = row.querySelector('.season-select');
  const kitSelect = row.querySelector('.kit-select');
  const sizeSelect = row.querySelector('.size-select');
  const sizeCustomInput = row.querySelector('.size-custom-input');
  const itemCustomInput = row.querySelector('.item-custom-input');
  const preview = row.querySelector('.item-preview');

  if (selectEl.value === '__custom__') {
    selectEl.disabled = true;
    selectEl.style.display = 'none';
    itemCustomInput.disabled = false;
    itemCustomInput.style.display = 'block';
    itemCustomInput.required = true;
    itemCustomInput.focus();

    seasonSelect.innerHTML = '<option value="">Não aplicável</option>';
    seasonSelect.required = false;
    toggleSeasonCustom(seasonSelect);
    kitSelect.innerHTML = '<option value="">Não aplicável</option>';
    kitSelect.required = false;
    toggleKitCustom(kitSelect);

    sizeSelect.disabled = true;
    sizeSelect.style.display = 'none';
    sizeSelect.required = false;
    sizeCustomInput.disabled = false;
    sizeCustomInput.style.display = 'block';
    sizeCustomInput.required = true;

    preview.removeAttribute('src');
    preview.style.display = 'none';
    updateVintageNoteField(row, false);
    return;
  }

  itemCustomInput.required = false;

  const selected = selectEl.options[selectEl.selectedIndex];
  updateVintageNoteField(row, selected.dataset.category === 'Vintage');

  const seasons = (selected.dataset.seasons || '')
    .split(',')
    .map(function (s) { return s.trim(); })
    .filter(Boolean);
  populateSeasonSelect(seasonSelect, seasons);
  seasonSelect.disabled = false;
  seasonSelect.style.display = '';
  seasonSelect.required = seasons.length > 0;
  toggleSeasonCustom(seasonSelect);

  const kitTypes = (selected.dataset.kitTypes || '')
    .split(',')
    .map(function (s) { return s.trim(); })
    .filter(Boolean);
  populateKitSelect(kitSelect, kitTypes);
  kitSelect.disabled = false;
  kitSelect.style.display = '';
  kitSelect.required = kitTypes.length > 0;
  toggleKitCustom(kitSelect);

  const sizes = (selected.dataset.sizes || '')
    .split(',')
    .map(function (s) { return s.trim(); })
    .filter(Boolean);

  sizeSelect.innerHTML = '';
  sizeSelect.disabled = false;
  sizeSelect.style.display = '';
  sizeSelect.required = sizes.length > 0;
  sizeCustomInput.disabled = true;
  sizeCustomInput.style.display = 'none';
  sizeCustomInput.required = false;

  const placeholder = document.createElement('option');
  placeholder.value = '';
  placeholder.disabled = sizes.length > 0;
  placeholder.selected = true;
  placeholder.textContent = sizes.length ? 'Escolhe um tamanho' : 'Sem tamanhos indicados';
  sizeSelect.appendChild(placeholder);

  sizes.forEach(function (s) {
    const opt = document.createElement('option');
    opt.value = s;
    opt.textContent = s;
    sizeSelect.appendChild(opt);
  });

  updatePreviewImage(selectEl);
}

function updateVintageNoteField(row, isVintage) {
  const field = row.querySelector('.vintage-note-field');
  const input = row.querySelector('.vintage-note-input');
  if (isVintage) {
    field.style.display = 'flex';
    input.required = true;
  } else {
    field.style.display = 'none';
    input.required = false;
    input.value = '';
  }
}

function updatePreviewImage(selectEl) {
  const row = selectEl.closest('.line-item');
  const itemSelect = row.querySelector('.item-select');
  const preview = row.querySelector('.item-preview');

  if (itemSelect.disabled || !itemSelect.value || itemSelect.value === '__custom__') {
    preview.removeAttribute('src');
    preview.style.display = 'none';
    return;
  }

  const selected = itemSelect.options[itemSelect.selectedIndex];
  let variantImages = {};
  try {
    variantImages = JSON.parse(selected.dataset.images || '{}');
  } catch (e) {
    variantImages = {};
  }

  const seasonVal = row.querySelector('.season-select').value;
  const kitVal = row.querySelector('.kit-select').value;
  const key = kitVal + '|' + seasonVal;
  const src = variantImages[key] || selected.dataset.image;

  if (src) {
    preview.src = src;
    preview.style.display = 'block';
  } else {
    preview.removeAttribute('src');
    preview.style.display = 'none';
  }
}

function togglePersonalize(checkbox) {
  const row = checkbox.closest('.line-item');
  const flag = row.querySelector('.personalize-flag');
  const text = row.querySelector('.personalize-text');
  if (checkbox.checked) {
    flag.value = '1';
    text.style.display = 'block';
    text.required = true;
    text.focus();
  } else {
    flag.value = '';
    text.style.display = 'none';
    text.required = false;
    text.value = '';
  }
}

function resetLineItem(row) {
  const itemSelect = row.querySelector('.item-select');
  const itemCustomInput = row.querySelector('.item-custom-input');
  const seasonSelect = row.querySelector('.season-select');
  const kitSelect = row.querySelector('.kit-select');
  const sizeSelect = row.querySelector('.size-select');
  const sizeCustomInput = row.querySelector('.size-custom-input');
  const preview = row.querySelector('.item-preview');
  const personalizeCheckbox = row.querySelector('.personalize-checkbox');
  const personalizeFlag = row.querySelector('.personalize-flag');
  const personalizeText = row.querySelector('.personalize-text');

  personalizeCheckbox.checked = false;
  personalizeFlag.value = '';
  personalizeText.value = '';
  personalizeText.required = false;
  personalizeText.style.display = 'none';

  itemSelect.selectedIndex = 0;
  itemSelect.disabled = false;
  itemSelect.style.display = '';
  itemCustomInput.value = '';
  itemCustomInput.disabled = true;
  itemCustomInput.style.display = 'none';
  itemCustomInput.required = false;

  seasonSelect.innerHTML = '<option value="" disabled selected>Escolhe um artigo primeiro</option>';
  seasonSelect.disabled = false;
  seasonSelect.style.display = '';
  seasonSelect.required = false;
  row.querySelector('.season-custom-flag').value = '';
  const seasonCustomInput = row.querySelector('.season-custom-input');
  seasonCustomInput.value = '';
  seasonCustomInput.disabled = true;
  seasonCustomInput.style.display = 'none';
  seasonCustomInput.required = false;

  kitSelect.innerHTML = '<option value="" disabled selected>Escolhe um artigo primeiro</option>';
  kitSelect.disabled = false;
  kitSelect.style.display = '';
  kitSelect.required = false;
  row.querySelector('.kit-custom-flag').value = '';
  const kitCustomInput = row.querySelector('.kit-custom-input');
  kitCustomInput.value = '';
  kitCustomInput.disabled = true;
  kitCustomInput.style.display = 'none';
  kitCustomInput.required = false;

  sizeSelect.innerHTML = '<option value="" disabled selected>Escolhe um artigo primeiro</option>';
  sizeSelect.disabled = false;
  sizeSelect.style.display = '';
  sizeSelect.required = false;
  sizeCustomInput.value = '';
  sizeCustomInput.disabled = true;
  sizeCustomInput.style.display = 'none';
  sizeCustomInput.required = false;

  row.querySelector('.qty-input').value = 1;
  preview.removeAttribute('src');
  preview.style.display = 'none';
  updateVintageNoteField(row, false);
  row.querySelector('.item-image-input').value = '';
}

function addLineItem() {
  const container = document.getElementById('items-container');
  const template = container.querySelector('.line-item');
  const clone = template.cloneNode(true);
  resetLineItem(clone);
  container.appendChild(clone);
  updateRemoveButtons();
}

function removeLineItem(btn) {
  const container = document.getElementById('items-container');
  const row = btn.closest('.line-item');
  if (container.children.length > 1) {
    row.remove();
  }
  updateRemoveButtons();
}

function updateRemoveButtons() {
  const rows = document.querySelectorAll('#items-container .line-item');
  rows.forEach(function (row) {
    row.querySelector('.remove-item-btn').style.display = rows.length > 1 ? 'inline-block' : 'none';
  });
}

// Replays a saved line item's selections into a blank .line-item row. Used by
// the edit-order page to reconstruct dropdown state from server-provided data,
// since browsers can't have selects pre-populated with JS-driven option lists.
function bootstrapEditLine(row, data) {
  const itemSelect = row.querySelector('.item-select');

  if (data.is_custom) {
    itemSelect.value = '__custom__';
    itemSelect.dispatchEvent(new Event('change'));
    row.querySelector('.item-custom-input').value = data.item_description;
  } else {
    itemSelect.value = data.item_description;
    itemSelect.dispatchEvent(new Event('change'));

    const seasonSelect = row.querySelector('.season-select');
    if (data.season) {
      const hasOption = Array.from(seasonSelect.options).some(function (o) { return o.value === data.season; });
      if (hasOption) {
        seasonSelect.value = data.season;
      } else {
        seasonSelect.value = '__custom__';
        seasonSelect.dispatchEvent(new Event('change'));
        row.querySelector('.season-custom-input').value = data.season;
      }
    }

    const kitSelect = row.querySelector('.kit-select');
    if (data.kit_type) {
      const hasOption = Array.from(kitSelect.options).some(function (o) { return o.value === data.kit_type; });
      if (hasOption) {
        kitSelect.value = data.kit_type;
      } else {
        kitSelect.value = '__custom__';
        kitSelect.dispatchEvent(new Event('change'));
        row.querySelector('.kit-custom-input').value = data.kit_type;
      }
    }

    if (data.size) {
      row.querySelector('.size-select').value = data.size;
    }

    updatePreviewImage(itemSelect);
  }

  row.querySelector('.qty-input').value = data.quantity || 1;

  if (data.personalization) {
    const checkbox = row.querySelector('.personalize-checkbox');
    checkbox.checked = true;
    togglePersonalize(checkbox);
    row.querySelector('.personalize-text').value = data.personalization;
  }

  if (data.item_note) {
    row.querySelector('.vintage-note-input').value = data.item_note;
  }

  if (data.item_image_url) {
    const label = row.querySelector('.item-image-label');
    if (label) {
      label.innerHTML = 'Já tens uma foto enviada — escolhe outra para a substituir. <a href="' + data.item_image_url + '" target="_blank" rel="noopener">Ver foto atual</a>';
    }
  }
}

function bootstrapEditForm(lines) {
  const container = document.getElementById('items-container');
  const template = container.querySelector('.line-item');

  lines.forEach(function (data, i) {
    const row = i === 0 ? template : (function () {
      const clone = template.cloneNode(true);
      resetLineItem(clone);
      container.appendChild(clone);
      return clone;
    })();
    bootstrapEditLine(row, data);
  });

  updateRemoveButtons();
}

updateRemoveButtons();
