"use strict";

/*
 * Category-picker regression: categoryPicker.selectCategory must ignore
 * unchecked radios.
 *
 * Why: a radio group fires `change` on the previously selected option when
 * the selection moves (real user clicks), and drafts.js replays synthetic
 * input/change events for every radio during draft restore. Script order in
 * base.html is workledger-ui.js -> alpine.min.js -> drafts.js, so Alpine's
 * @change/x-model handlers are already bound when drafts.js restores saved
 * state on DOMContentLoaded. The last change event wins, which means an
 * unchecked radio's handler can overwrite the just-selected category and
 * make the trigger label lie about the checked value that will be POSTed.
 *
 * This test loads the real workledger-ui.js and drafts.js sources and
 * drives the exact production event order; no browser needed.
 */

const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const REPO_ROOT = path.resolve(__dirname, "..", "..");
const UI_JS = path.join(REPO_ROOT, "static/js/workledger-ui.js");
const DRAFTS_JS = path.join(REPO_ROOT, "static/js/drafts.js");

const uiSource = fs.readFileSync(UI_JS, "utf8");
const draftsSource = fs.readFileSync(DRAFTS_JS, "utf8");

function makeRadio(value, initiallyChecked) {
  const listeners = { change: [], input: [] };
  return {
    name: "category",
    type: "radio",
    value,
    checked: initiallyChecked,
    addEventListener(type, fn) {
      listeners[type].push(fn);
    },
    dispatchEvent(event) {
      for (const fn of listeners[event.type]) fn.call(this, event);
      return true;
    },
    closest() {
      // Mirrors the label wrapper the template uses for the readable name.
      return { textContent: value };
    },
  };
}

function runHarness({ draft, radios }) {
  // document stub: workledger-ui.js registers on alpine:init, drafts.js on
  // DOMContentLoaded; both handlers are captured and fired in production order.
  const handlers = {};
  const form = {
    dataset: { draftKey: "expense-entry" },
    elements: radios,
    addEventListener() {},
  };
  const document = {
    addEventListener(type, fn) {
      (handlers[type] ||= []).push(fn);
    },
    querySelectorAll(selector) {
      if (selector === "form[data-draft-key]") return [form];
      return [];
    },
  };

  const registered = {};
  const Alpine = {
    data(name, factory) {
      registered[name] = factory;
    },
  };

  const localStorage = {
    getItem() {
      return JSON.stringify(draft);
    },
    setItem() {},
    removeItem() {},
  };

  class FakeEvent {
    constructor(type) {
      this.type = type;
    }
  }

  const context = vm.createContext({
    document,
    window: { requestAnimationFrame: (cb) => cb() },
    Alpine,
    localStorage,
    Event: FakeEvent,
    console,
  });

  // Production order 1: workledger-ui.js registers its alpine:init listener,
  // then Alpine loads and fires it (component factories registered).
  vm.runInContext(uiSource, context);
  for (const fn of handlers["alpine:init"]) fn();

  // Simulate Alpine initialising the component on the page: instantiate the
  // picker and bind the radio handlers exactly like the template does, with
  // x-model bound before @change (attribute order in expense_entry.html).
  const picker = registered.categoryPicker();
  picker.$root = { querySelectorAll: () => [] };
  picker.$refs = {};
  picker.$nextTick = (cb) => cb();
  for (const radio of radios) {
    radio.addEventListener("change", () => {
      if (radio.checked) picker.categoryCode = radio.value; // x-model
    });
    radio.addEventListener("change", () => picker.selectCategory(radio)); // @change
  }

  // Production order 2: drafts.js registers its DOMContentLoaded listener and
  // the browser fires DOMContentLoaded — restoring saved state afterwards.
  vm.runInContext(draftsSource, context);
  for (const fn of handlers["DOMContentLoaded"]) fn();

  return picker;
}

function testDraftRestoreIgnoresUncheckedRadios() {
  // Draft saved with taxi selected. DOM order puts the checked radio first,
  // so during restore the unchecked radio's change event fires last — the
  // harmful ordering that previously overwrote the selection.
  const picker = runHarness({
    draft: { "category:taxi": true, "category:meal_actual": false },
    radios: [makeRadio("taxi", false), makeRadio("meal_actual", false)],
  });
  assert.strictEqual(picker.categoryCode, "taxi");
  assert.strictEqual(picker.selectedLabel, "taxi");
}

function testUserClickOrderIgnoresUncheckedRadio() {
  // Real interaction: user checks taxi while meal_actual was selected; the
  // browser fires change on both — the newly checked one first, then the
  // one that just became unchecked. The unchecked event must not win.
  const picker = runHarness({ draft: {}, radios: [] });
  picker.selectCategory(makeRadio("taxi", true));
  picker.selectCategory(makeRadio("meal_actual", false));
  assert.strictEqual(picker.categoryCode, "taxi");
  assert.strictEqual(picker.selectedLabel, "taxi");
}

testDraftRestoreIgnoresUncheckedRadios();
testUserClickOrderIgnoresUncheckedRadio();
console.log("category_picker_event_order: all assertions passed");
