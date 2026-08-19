(() => {
  const MAX_AGE_MS = 14 * 24 * 60 * 60 * 1000;
  const DEBOUNCE_MS = 250;
  const fields = (form) => [...form.elements].filter((field) =>
    field.name && field.type !== "file" && field.name !== "csrfmiddlewaretoken"
  );
  const fieldKey = (field) => (field.type === "checkbox" || field.type === "radio")
    ? field.name + ":" + field.value : field.name;
  const read = (form) => {
    const values = {};
    for (const field of fields(form)) {
      values[fieldKey(field)] = (field.type === "checkbox" || field.type === "radio")
        ? field.checked : field.value;
    }
    return values;
  };
  const save = (form, key) => {
    localStorage.setItem(key, JSON.stringify({ savedAt: Date.now(), values: read(form) }));
  };
  const clearSuccessfulDraft = () => {
    document.querySelectorAll("[data-draft-clear]").forEach((marker) => {
      if (marker.dataset.draftClear) localStorage.removeItem("workledger:draft:" + marker.dataset.draftClear);
    });
  };
  document.addEventListener("DOMContentLoaded", () => {
    clearSuccessfulDraft();
    document.querySelectorAll("form[data-draft-key]").forEach((form) => {
      const key = "workledger:draft:" + form.dataset.draftKey;
      let timer;
      try {
        const stored = JSON.parse(localStorage.getItem(key) || "null");
        const legacy = stored && !stored.values;
        const draft = stored && stored.values
          ? stored
          : { savedAt: legacy ? Date.now() : 0, values: stored || {} };
        if (!draft.savedAt || Date.now() - draft.savedAt > MAX_AGE_MS) {
          localStorage.removeItem(key);
        } else {
          for (const field of fields(form)) {
            const valueKey = fieldKey(field);
            if (!(valueKey in draft.values)) continue;
            const value = draft.values[valueKey];
            if (value === "" && field.value) continue;
            if (field.type === "checkbox" || field.type === "radio") field.checked = Boolean(value);
            else field.value = value;
            field.dispatchEvent(new Event("input", { bubbles: true }));
            field.dispatchEvent(new Event("change", { bubbles: true }));
          }
          document.dispatchEvent(new CustomEvent("workledger:draft-restored", { detail: { form } }));
        }
      } catch (_) { localStorage.removeItem(key); }
      form.addEventListener("input", () => {
        window.clearTimeout(timer);
        timer = window.setTimeout(() => save(form, key), DEBOUNCE_MS);
      });
      form.addEventListener("change", () => {
        window.clearTimeout(timer);
        timer = window.setTimeout(() => save(form, key), DEBOUNCE_MS);
      });
    });
  });
})();
