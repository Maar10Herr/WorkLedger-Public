/* WorkLedger lightweight UI behaviours (UX pass 1).
 * Alpine components only; no frameworks, no network assets.
 * - wlMenu: accessible popover/bottom-sheet menu. Closes on outside click
 *   and Escape, focuses the first item when opened, traps Tab focus while
 *   open, and restores focus to the trigger on close.
 * - wlFormState: disables the submit control while a form is submitting.
 */
(() => {
  document.addEventListener("alpine:init", () => {
    Alpine.data("wlMenu", () => ({
      open: false,

      toggle() {
        this.open ? this.close() : this.openMenu();
      },

      openMenu() {
        this.open = true;
        this.$nextTick(() => {
          // WebKit needs one painted frame after x-show removes display:none
          // before links inside the sheet reliably accept focus.
          window.requestAnimationFrame(() => this.focusFirst());
        });
      },

      close() {
        this.open = false;
        if (this.$refs.trigger) this.$refs.trigger.focus();
      },

      items() {
        const panel = this.$refs.panel;
        if (!panel) return [];
        return Array.from(panel.querySelectorAll('[role="menuitem"]'));
      },

      focusFirst() {
        const items = this.items();
        if (items[0]) items[0].focus();
      },

      focusLast() {
        const items = this.items();
        if (items.length) items[items.length - 1].focus();
      },

      trapFocus(event) {
        if (event.key !== "Tab") return;
        const items = this.items();
        if (!items.length) return;
        event.preventDefault();
        const index = items.indexOf(document.activeElement);
        const next = event.shiftKey
          ? index <= 0
            ? items[items.length - 1]
            : items[index - 1]
          : index >= items.length - 1
            ? items[0]
            : items[index + 1];
        next.focus();
      },

      onTriggerKeydown(event) {
        if (event.key === "ArrowDown") {
          event.preventDefault();
          this.openMenu();
        } else if (event.key === "ArrowUp") {
          event.preventDefault();
          this.open = true;
          this.$nextTick(() => {
            window.requestAnimationFrame(() => this.focusLast());
          });
        }
      },
    }));

    Alpine.data("wlFormState", () => ({
      submitting: false,
      init() {
        this.$el.addEventListener("submit", () => {
          this.submitting = true;
        });
      },
    }));

    /* History filter sheet (UX pass 4): focus-managed dialog. Tab is
     * trapped inside the panel, Escape and outside clicks close it, and
     * focus returns to the Filters trigger. The panel CSS keeps it inside
     * the viewport. */
    Alpine.data("filterSheet", () => ({
      open: false,

      toggle() {
        this.open ? this.close() : this.openSheet();
      },

      openSheet() {
        this.open = true;
        this.$nextTick(() => {
          // WebKit needs a painted frame after x-show before focus works.
          window.requestAnimationFrame(() => {
            const first = this.$refs.panel.querySelector(
              'input, select, button, a[href], textarea, [tabindex]:not([tabindex="-1"])'
            );
            if (first) first.focus();
          });
        });
      },

      close() {
        this.open = false;
        if (this.$refs.trigger) this.$refs.trigger.focus();
      },

      trapFocus(event) {
        if (event.key !== "Tab") return;
        const focusables = this.$refs.panel.querySelectorAll(
          'input, select, button, a[href], textarea, [tabindex]:not([tabindex="-1"])'
        );
        if (!focusables.length) return;
        event.preventDefault();
        const index = Array.from(focusables).indexOf(document.activeElement);
        const next = event.shiftKey
          ? index <= 0
            ? focusables[focusables.length - 1]
            : focusables[index - 1]
          : index >= focusables.length - 1
            ? focusables[0]
            : focusables[index + 1];
        next.focus();
      },
    }));

    /* Correction form (UX pass 4): journey location pickers keep the hidden
     * name/type inputs in sync with the selected location, so the correction
     * POST stores human names, not only raw ids. */
    Alpine.data("correctionForm", () => ({
      setLocationNames(select, nameField, typeField) {
        const option = select.selectedOptions[0];
        const set = (name, value) => {
          const input = this.$el.querySelector(`[name="${name}"]`);
          if (input) input.value = value;
        };
        if (nameField) set(nameField, (option && option.dataset.name) || "");
        if (typeField) set(typeField, (option && option.dataset.type) || "");
      },
    }));

    /* Journey decision tree (UX pass 2): destination/origin/time pickers,
     * persisted transport mode, train selection, and rail-pass coverage. */
    Alpine.data("journeyForm", () => ({
      transportMode: localStorage.getItem("workledger.transportMode") || "train",
      destinationId: "",
      originId: "",
      originPickerOpen: false,
      originName: "",
      originLocality: "",
      originStation: "",
      timePickerOpen: false,
      timeValue: "",
      dateLabel: "",
      transportMore: false,
      destinationStation: "",
      selectedTrain: null,
      passPickerOpen: false,
      coveredByPass: false,
      activePassName: "",
      carRouteSummary: "",
      taxRelevant: true,
      employerReimbursable: false,

      init() {
        const read = (selector) => {
          const element = this.$root.querySelector(selector);
          return element ? element.textContent.trim() : "";
        };
        this.originName = read("[data-origin-name]");
        this.originLocality = read("[data-origin-locality]");
        this.timeValue = read("[data-time-value]");
        this.dateLabel = read("[data-date-label]") || "today";
        this.activePassName = read("[data-pass-name]");
        const originStationInput = this.$root.querySelector('[name="origin_station"]');
        this.originStation = originStationInput ? originStationInput.value : "";
        const summary = this.$root.querySelector("[data-rail-pass-summary]");
        this.coveredByPass = summary ? summary.dataset.covered === "true" : false;
        const primary = [
          "train",
          "private_car",
          "taxi",
          "local_public_transport",
          "passenger",
          "walking",
          "bicycle",
        ];
        this.transportMore = !primary.includes(this.transportMode);
        this.$watch("transportMode", (value) => {
          localStorage.setItem("workledger.transportMode", value);
        });
        // Add-location return flow: the server renders
        // data-initial-destination="<uuid>" when the user returns via
        // ?new_location=<uuid>. Preselect that destination radio and sync the
        // Alpine state, deferred past drafts.js' draft restore so a stale
        // saved destination never overrides the freshly added location (the
        // rest of the draft is preserved either way).
        const initialDestination =
          this.$root.getAttribute("data-initial-destination") || "";
        if (initialDestination) {
          setTimeout(() => {
            const target = this.$root.querySelector(
              `input[name="destination"][value="${initialDestination}"]`
            );
            if (target) {
              target.checked = true;
              this.destinationId = initialDestination;
              this.onDestinationChange(target);
            }
          }, 0);
        }
      },

      onDestinationChange(input) {
        const station = input.getAttribute("data-station") || "";
        if (station) this.destinationStation = station;
        const kilometres = input.getAttribute("data-route-km") || "";
        this.carRouteSummary = kilometres ? `${kilometres} km` : "";
      },

      onOriginChange(input) {
        this.originName = input.getAttribute("data-name") || "";
        this.originLocality = input.getAttribute("data-locality") || "";
        this.originStation = input.getAttribute("data-station") || "";
      },

      onTimeChange(input) {
        const value = input.value || "";
        this.timeValue = value.slice(11, 16);
        const parsed = value ? new Date(value) : null;
        this.dateLabel =
          parsed && parsed.toDateString() !== new Date().toDateString()
            ? parsed.toLocaleDateString(undefined, { day: "numeric", month: "short" })
            : "today";
      },

      selectTrain(input) {
        this.selectedTrain = {
          category: input.dataset.category || "",
          number: input.dataset.number || "",
          departure: input.dataset.departure || "",
          route: input.dataset.route || "",
          delay: input.dataset.delay || "",
        };
      },

      onPassChange(input) {
        this.coveredByPass = input.value !== "none";
        this.activePassName = input.dataset.name || "";
      },
    }));

    /* External activity (UX pass 2): journey-linked continuation with
     * progressive disclosures for meals, return time, and output tracks. */
    Alpine.data("activityForm", () => ({
      meals: { breakfast: false, lunch: false, dinner: false },
      stillOngoing: false,
      taxRelevant: true,
      employerReimbursable: false,
    }));

    /* Expense entry (UX pass 3): amount-aware sticky save and the advanced
     * `more details` disclosure. */
    Alpine.data("expenseForm", () => ({
      amount: "",
      taxRelevant: false,
      employerReimbursable: false,
      advancedOpen: false,
    }));

    /* Category bottom sheet (UX pass 3): grouped/recent/searchable picker.
     * Server-rendered options are filtered in place; the sheet traps Tab,
     * closes on Escape or scrim click, restores focus to the trigger, and
     * stays within the viewport via the .wl-sheet panel CSS. */
    Alpine.data("categoryPicker", () => ({
      open: false,
      query: "",
      categoryCode: "",
      selectedLabel: "choose category",

      init() {
        const syncSelection = () => {
          const checked = this.$root.querySelector(
            'input[name="category"]:checked'
          );
          if (!checked) return;
          this.categoryCode = checked.value;
          const option = checked.closest("label");
          this.selectedLabel = option
            ? option.textContent.trim()
            : this.categoryCode;
        };
        syncSelection();
        // drafts.js restores persisted radio state on DOMContentLoaded. Run
        // after that restoration so the trigger label matches the checked
        // radio even when Alpine initialized first.
        document.addEventListener(
          "DOMContentLoaded",
          () => window.setTimeout(syncSelection, 0),
          { once: true }
        );
        document.addEventListener("workledger:draft-restored", syncSelection);
        const selectionTimer = window.setInterval(() => {
          syncSelection();
          if (this.$root.querySelector('input[name="category"]:checked')) {
            window.clearInterval(selectionTimer);
          }
        }, 25);
        window.setTimeout(() => window.clearInterval(selectionTimer), 2000);
      },

      toggle() {
        this.open ? this.close() : this.openPicker();
      },

      openPicker() {
        this.open = true;
        this.$nextTick(() => {
          // WebKit needs a painted frame after x-show before focus works.
          window.requestAnimationFrame(() => {
            const search = this.$refs.search;
            if (search) search.focus();
            this.filterOptions();
          });
        });
      },

      close() {
        this.open = false;
        this.query = "";
        this.filterOptions();
        if (this.$refs.trigger) this.$refs.trigger.focus();
      },

      selectCategory(input) {
        // Radio groups fire `change` on the previously checked option when
        // the selection moves, and drafts.js replays synthetic change events
        // for every radio during draft restore. Only the checked radio may
        // select: an unchecked one's event would overwrite the just-chosen
        // category and make the trigger label lie about what is POSTed.
        if (!input.checked) return;
        this.categoryCode = input.value;
        const option = input.closest("label");
        this.selectedLabel = option
          ? option.textContent.trim()
          : this.categoryCode;
        this.close();
      },

      filterOptions() {
        const query = this.query.trim().toLowerCase();
        const options = this.$root.querySelectorAll("[data-category-option]");
        options.forEach((option) => {
          const name = (option.getAttribute("data-category-name") || "").toLowerCase();
          option.hidden = Boolean(query) && !name.includes(query);
        });
        this.$root
          .querySelectorAll("[data-category-group], [data-category-recent]")
          .forEach((group) => {
            const visible = Array.from(
              group.querySelectorAll("[data-category-option]")
            ).some((option) => !option.hidden);
            group.hidden = Boolean(query) && !visible;
          });
      },

      trapFocus(event) {
        if (event.key !== "Tab") return;
        const focusables = this.$refs.panel.querySelectorAll(
          'input, button, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        if (!focusables.length) return;
        event.preventDefault();
        const index = Array.from(focusables).indexOf(document.activeElement);
        const next = event.shiftKey
          ? index <= 0
            ? focusables[focusables.length - 1]
            : focusables[index - 1]
          : index >= focusables.length - 1
            ? focusables[0]
            : focusables[index + 1];
        next.focus();
      },
    }));

    /* Receipt-only (UX pass 3): native file choice with an object-URL image
     * preview where the browser supports it, plus output track toggles. */
    Alpine.data("receiptForm", () => ({
      fileName: "",
      fileSize: "",
      previewUrl: "",
      taxRelevant: false,
      employerReimbursable: false,

      onFileChange(input) {
        if (this.previewUrl) URL.revokeObjectURL(this.previewUrl);
        this.previewUrl = "";
        const file = input.files && input.files[0];
        if (!file) {
          this.fileName = "";
          this.fileSize = "";
          return;
        }
        this.fileName = file.name;
        this.fileSize = file.size ? `${Math.max(1, Math.round(file.size / 1024))} KB` : "";
        if (file.type.startsWith("image/")) {
          this.previewUrl = URL.createObjectURL(file);
        }
      },
    }));

    Alpine.data("exportPurpose", () => ({
      selectFormat(kind) {
        const input = this.$el.querySelector(`input[name="kind"][value="${kind}"]`);
        if (input) input.checked = true;
      },
    }));
  });

  const syncCategoryLabels = () => {
    document.querySelectorAll("[data-category-picker]").forEach((picker) => {
      const checked = picker.querySelector('input[name="category"]:checked');
      if (!checked) return;
      const label = picker.querySelector(".wl-category-trigger__label");
      const option = checked.closest("label");
      if (label && option) label.textContent = option.textContent.trim();
    });
  };
  document.addEventListener("DOMContentLoaded", () => {
    if (typeof window.setTimeout === "function") {
      window.setTimeout(syncCategoryLabels, 0);
    } else {
      syncCategoryLabels();
    }
  });
  document.addEventListener("workledger:draft-restored", syncCategoryLabels);
  document.addEventListener("alpine:initialized", syncCategoryLabels);
})();
