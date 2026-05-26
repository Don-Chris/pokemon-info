(() => {
  const data = window.__SOULLINK_DATA__ || {};
  const statusOptions = window.__SOULLINK_STATUS__ || [];
  const code = window.__SOULLINK_CODE__;

  const body = document.getElementById("soul-body");
  const sessionTitle = document.getElementById("session-title");
  const addRowButton = document.getElementById("add-row");
  const saveHint = document.getElementById("save-hint");
  const locationDatalist = document.getElementById("location-suggestions");
  const pokemonDatalist = document.getElementById("pokemon-suggestions");
  const languageSelect = document.getElementById("language-select");

  let saveTimer = null;
  let locationTimer = null;
  let pokemonTimer = null;
  let saveRequestSeq = 0;
  let lastAppliedSaveSeq = 0;
  let syncTimer = null;
  let saveInFlight = false;
  let localDirty = false;
  let lastServerUpdatedAt = Number(data.updated_at || 0);

  const statusClass = (value) => {
    const normalized = String(value || "").toLowerCase();
    if (normalized === "aktiv") return "status-active";
    if (normalized === "im pc") return "status-pc";
    return "status-muted";
  };

  const updateRowStatusClass = (rowEl, status) => {
    rowEl.classList.remove("status-active", "status-pc", "status-muted");
    rowEl.classList.add(statusClass(status));
  };

  const collectPayload = () => {
    const playerInputs = Array.from(document.querySelectorAll(".player-name"));
    const playerNames = playerInputs.map((input) => input.value.trim() || "");

    const rows = Array.from(body.querySelectorAll(".soul-row")).map((rowEl) => {
      const locationInput = rowEl.querySelector(".location-input");
      const statusSelect = rowEl.querySelector(".status-select");
      const pokemonInputs = Array.from(rowEl.querySelectorAll(".pokemon-input"));
      return {
        id: rowEl.dataset.rowId,
        location: locationInput ? locationInput.value.trim() : "",
        status: statusSelect ? statusSelect.value : "",
        is_starter: rowEl.dataset.starter === "true",
        players: pokemonInputs.map((input) => ({ name: input.value.trim() })),
      };
    });

    return {
      session_name: sessionTitle ? sessionTitle.textContent.trim() : (data.session_name || ""),
      language: languageSelect ? languageSelect.value : data.language || "",
      player_names: playerNames,
      rows,
    };
  };

  const updateValueClass = (input) => {
    if (!input) return;
    input.classList.toggle("has-value", Boolean(input.value && input.value.trim()));
  };

  const bindSpriteFallback = (spriteEl) => {
    if (!spriteEl || spriteEl.dataset.fallbackBound === "true") {
      return;
    }
    spriteEl.dataset.fallbackBound = "true";
    spriteEl.addEventListener("error", () => {
      spriteEl.removeAttribute("src");
      spriteEl.classList.add("is-empty");
    });
  };

  const applyErrors = (errors) => {
    const rows = Array.from(body.querySelectorAll(".soul-row"));
    rows.forEach((rowEl) => {
      const errorBox = rowEl.querySelector(".row-errors");
      if (!errorBox) return;
      const messages = errors[rowEl.dataset.rowId] || [];
      errorBox.textContent = messages.join(" ");
    });
  };

  const save = async () => {
    if (!code) return;
    const requestSeq = ++saveRequestSeq;
    saveInFlight = true;
    saveHint.textContent = "Speichern...";
    const payload = collectPayload();
    try {
      const response = await fetch(`/soullink/${code}/save`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const dataOut = await response.json();
      if (requestSeq < lastAppliedSaveSeq) {
        return;
      }
      lastAppliedSaveSeq = requestSeq;
      if (dataOut?.errors) {
        applyErrors(dataOut.errors);
      }
      if (Array.isArray(dataOut?.rows)) {
        applyRows(dataOut.rows);
      }
      if (typeof dataOut?.language === "string") {
        data.language = dataOut.language;
      }
      if (typeof dataOut?.session_name === "string") {
        data.session_name = dataOut.session_name;
      }
      if (typeof dataOut?.updated_at === "number") {
        lastServerUpdatedAt = Math.max(lastServerUpdatedAt, dataOut.updated_at);
      }
      localDirty = false;
      saveHint.textContent = "Gespeichert.";
      setTimeout(() => {
        saveHint.textContent = "Alle Änderungen werden automatisch gespeichert.";
      }, 1200);
    } catch {
      saveHint.textContent = "Speichern fehlgeschlagen.";
    } finally {
      saveInFlight = false;
    }
  };

  const scheduleSave = () => {
    if (saveTimer) clearTimeout(saveTimer);
    localDirty = true;
    saveTimer = setTimeout(save, 2000);
  };

  const buildRow = (row) => {
    const tr = document.createElement("tr");
    tr.className = `soul-row ${statusClass(row.status)}`;
    tr.dataset.rowId = row.id;
    tr.dataset.starter = row.is_starter ? "true" : "false";

    const locationTd = document.createElement("td");
    const locationInput = document.createElement("input");
    locationInput.className = "location-input";
    locationInput.value = row.location?.display || row.location || "";
    locationInput.setAttribute("list", "location-suggestions");
    updateValueClass(locationInput);
    if (row.is_starter) {
      locationInput.readOnly = true;
    }
    const errorBox = document.createElement("div");
    errorBox.className = "row-errors";
    locationTd.appendChild(locationInput);
    locationTd.appendChild(errorBox);

    const statusTd = document.createElement("td");
    const statusSelect = document.createElement("select");
    statusSelect.className = "status-select";
    statusOptions.forEach((option) => {
      const opt = document.createElement("option");
      opt.value = option;
      opt.textContent = option;
      if (option === row.status) opt.selected = true;
      statusSelect.appendChild(opt);
    });
    statusTd.appendChild(statusSelect);

    tr.appendChild(locationTd);
    tr.appendChild(statusTd);

    row.players.forEach((player) => {
      const td = document.createElement("td");
      const wrap = document.createElement("div");
      wrap.className = "pokemon-cell";
      const sprite = document.createElement("img");
      sprite.className = "pokemon-sprite";
      sprite.alt = "";
      bindSpriteFallback(sprite);
      const spriteSrc = player?.sprite || "";
      if (spriteSrc) {
        sprite.src = spriteSrc;
      } else {
        sprite.classList.add("is-empty");
      }
      const input = document.createElement("input");
      input.className = "pokemon-input";
      input.setAttribute("list", "pokemon-suggestions");
      input.value = typeof player === "string" ? player : (player?.name || "");
      updateValueClass(input);
      wrap.appendChild(sprite);
      wrap.appendChild(input);
      td.appendChild(wrap);
      tr.appendChild(td);
    });

    const actionTd = document.createElement("td");
    if (row.is_starter) {
      const locked = document.createElement("span");
      locked.className = "locked";
      locked.textContent = "Starter";
      actionTd.appendChild(locked);
    } else {
      const del = document.createElement("button");
      del.type = "button";
      del.className = "delete-row";
      del.textContent = "Löschen";
      actionTd.appendChild(del);
    }
    tr.appendChild(actionTd);

    return tr;
  };

  const addRow = () => {
    const newRow = {
      id: `client-${Date.now()}-${Math.floor(Math.random() * 10000)}`,
      location: "",
      status: "aktiv",
      is_starter: false,
      players: Array.from({ length: data.players || 2 }, () => ({ name: "", sprite: "" })),
    };
    const rowEl = buildRow(newRow);
    body.appendChild(rowEl);
    scheduleSave();
  };

  const applyRows = (rows) => {
    const byId = new Map(rows.map((row) => [String(row.id), row]));
    const rowElements = Array.from(body.querySelectorAll(".soul-row"));
    const activeElement = document.activeElement;

    const isFocusedInput = (input) => activeElement === input;

    rowElements.forEach((rowEl) => {
      const row = byId.get(String(rowEl.dataset.rowId));
      if (!row) return;

      const locationInput = rowEl.querySelector(".location-input");
      if (locationInput && !isFocusedInput(locationInput)) {
        locationInput.value = row.location?.display || "";
        updateValueClass(locationInput);
      }

      const statusSelect = rowEl.querySelector(".status-select");
      if (statusSelect && row.status && !isFocusedInput(statusSelect)) {
        statusSelect.value = row.status;
      }
      updateRowStatusClass(rowEl, row.status || "");

      const playerInputs = Array.from(rowEl.querySelectorAll(".pokemon-input"));
      const playerSprites = Array.from(rowEl.querySelectorAll(".pokemon-sprite"));
      (row.players || []).forEach((player, idx) => {
        const input = playerInputs[idx];
        if (input && !isFocusedInput(input)) {
          input.value = player?.name || "";
          updateValueClass(input);
        }
        const sprite = playerSprites[idx];
        if (sprite) {
          bindSpriteFallback(sprite);
          const spriteSrc = player?.sprite || "";
          if (spriteSrc) {
            sprite.src = spriteSrc;
            sprite.classList.remove("is-empty");
          } else {
            sprite.removeAttribute("src");
            sprite.classList.add("is-empty");
          }
        }
      });
    });
  };

  const applyPlayerNames = (playerNames) => {
    if (!Array.isArray(playerNames)) return;
    const activeElement = document.activeElement;
    document.querySelectorAll(".player-name").forEach((input, idx) => {
      if (activeElement === input) {
        return;
      }
      input.value = playerNames[idx] || "";
      updateValueClass(input);
    });
  };

  const reconcileRows = (rows) => {
    if (!Array.isArray(rows)) return;
    const existing = new Map(Array.from(body.querySelectorAll(".soul-row")).map((rowEl) => [String(rowEl.dataset.rowId), rowEl]));
    const incomingIds = new Set(rows.map((row) => String(row.id)));

    for (const [rowId, rowEl] of existing.entries()) {
      if (!incomingIds.has(rowId)) {
        rowEl.remove();
      }
    }

    rows.forEach((row) => {
      const rowId = String(row.id);
      if (!existing.has(rowId)) {
        body.appendChild(buildRow(row));
      }
    });
  };

  const applyRemoteState = (state) => {
    if (!state || !state.changed) {
      return;
    }
    if (typeof state.updated_at === "number") {
      lastServerUpdatedAt = Math.max(lastServerUpdatedAt, state.updated_at);
    }
    if (typeof state.language === "string") {
      data.language = state.language;
      if (languageSelect && languageSelect.value !== state.language && document.activeElement !== languageSelect) {
        languageSelect.value = state.language;
      }
    }
    if (typeof state.session_name === "string") {
      data.session_name = state.session_name;
      if (sessionTitle && document.activeElement !== sessionTitle) {
        sessionTitle.textContent = state.session_name || sessionTitle.dataset.defaultTitle || "";
      }
    }
    applyPlayerNames(state.player_names || []);
    reconcileRows(state.rows || []);
    applyRows(state.rows || []);
    const errors = {};
    (state.rows || []).forEach((row) => {
      errors[row.id] = row.errors || [];
    });
    applyErrors(errors);
  };

  const pollState = async () => {
    if (!code || localDirty || saveInFlight) {
      return;
    }
    try {
      const response = await fetch(`/soullink/${code}/state?since=${encodeURIComponent(lastServerUpdatedAt)}`);
      if (!response.ok) {
        return;
      }
      const payload = await response.json();
      applyRemoteState(payload);
    } catch {
      // ignore transient sync errors
    }
  };

  const updateLocationSuggestions = (value) => {
    if (locationTimer) clearTimeout(locationTimer);
    locationTimer = setTimeout(async () => {
      if (!value) {
        locationDatalist.innerHTML = "";
        return;
      }
      const language = languageSelect ? languageSelect.value : data.language || "";
      const generation = data.generation || "";
      const versionGroup = data.version_group || "";
      const response = await fetch(
        `/soullink/suggest/location?query=${encodeURIComponent(value)}&language=${encodeURIComponent(language)}&generation=${encodeURIComponent(generation)}&version_group=${encodeURIComponent(versionGroup)}`
      );
      const payload = await response.json();
      locationDatalist.innerHTML = "";
      for (const item of payload.results || []) {
        const option = document.createElement("option");
        option.value = item;
        locationDatalist.appendChild(option);
      }
    }, 150);
  };

  const updatePokemonSuggestions = (value) => {
    if (pokemonTimer) clearTimeout(pokemonTimer);
    pokemonTimer = setTimeout(async () => {
      if (!value) {
        pokemonDatalist.innerHTML = "";
        return;
      }
      const language = languageSelect ? languageSelect.value : data.language || "";
      const response = await fetch(`/suggest?query=${encodeURIComponent(value)}&generation=${encodeURIComponent(data.generation || "")}&language=${encodeURIComponent(language)}`);
      const payload = await response.json();
      pokemonDatalist.innerHTML = "";
      for (const item of payload.results || []) {
        const option = document.createElement("option");
        option.value = item;
        pokemonDatalist.appendChild(option);
      }
    }, 150);
  };

  body.addEventListener("input", (event) => {
    const target = event.target;
    if (target.classList.contains("location-input")) {
      updateLocationSuggestions(target.value.trim());
      updateValueClass(target);
      scheduleSave();
    }
    if (target.classList.contains("pokemon-input")) {
      updatePokemonSuggestions(target.value.trim());
      updateValueClass(target);
      scheduleSave();
    }
  });

  body.addEventListener("change", (event) => {
    const target = event.target;
    if (target.classList.contains("status-select")) {
      const rowEl = target.closest(".soul-row");
      if (rowEl) updateRowStatusClass(rowEl, target.value);
      scheduleSave();
    }
  });

  body.addEventListener("click", (event) => {
    const target = event.target;
    if (target.classList.contains("delete-row")) {
      const rowEl = target.closest(".soul-row");
      if (rowEl) {
        rowEl.remove();
        scheduleSave();
      }
    }
  });

  document.querySelectorAll(".player-name").forEach((input) => {
    updateValueClass(input);
    input.addEventListener("input", scheduleSave);
  });

  body.querySelectorAll(".location-input, .pokemon-input").forEach((input) => {
    updateValueClass(input);
  });

  body.querySelectorAll(".pokemon-sprite").forEach((sprite) => {
    bindSpriteFallback(sprite);
  });

  languageSelect?.addEventListener("change", () => {
    data.language = languageSelect.value;
    locationDatalist.innerHTML = "";
    pokemonDatalist.innerHTML = "";
    scheduleSave();
  });

  addRowButton?.addEventListener("click", addRow);

  if (sessionTitle) {
    sessionTitle.addEventListener("dblclick", () => {
      sessionTitle.setAttribute("contenteditable", "true");
      sessionTitle.focus();
      const selection = window.getSelection();
      if (selection) {
        selection.selectAllChildren(sessionTitle);
      }
    });

    sessionTitle.addEventListener("blur", () => {
      sessionTitle.removeAttribute("contenteditable");
      const fallback = sessionTitle.dataset.defaultTitle || "";
      const cleaned = (sessionTitle.textContent || "").trim() || fallback;
      sessionTitle.textContent = cleaned;
      data.session_name = cleaned;
      scheduleSave();
    });

    sessionTitle.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        sessionTitle.blur();
      }
      if (event.key === "Escape") {
        event.preventDefault();
        sessionTitle.textContent = data.session_name || sessionTitle.dataset.defaultTitle || "";
        sessionTitle.removeAttribute("contenteditable");
        sessionTitle.blur();
      }
    });
  }

  syncTimer = setInterval(pollState, 2500);

  const copyButton = document.getElementById("copy-link");
  const linkInput = document.getElementById("share-link");
  copyButton?.addEventListener("click", async () => {
    const url = `${window.location.origin}${linkInput.value}`;
    try {
      await navigator.clipboard.writeText(url);
      copyButton.textContent = "Kopiert";
      setTimeout(() => {
        copyButton.textContent = "Kopieren";
      }, 1200);
    } catch {
      linkInput.select();
      document.execCommand("copy");
    }
  });
})();
