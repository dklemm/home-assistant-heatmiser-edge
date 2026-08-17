/**
 * Heatmiser EDGE weekly programme card.
 *
 * Reads the grid from `sensor.<name>_weekly_program`'s attributes and writes it
 * back with the `heatmiser_edge.set_schedule` action. Nothing else: the card
 * holds no protocol knowledge of its own, so a register never has to be
 * understood in two languages.
 *
 * No build step and no imports. A custom integration cannot ship a bundler, and
 * the usual workaround - reaching into an existing element's prototype chain to
 * borrow LitElement - breaks whenever the frontend reshuffles. This is a table
 * of inputs; plain DOM is enough, and it will still work in five years.
 *
 * Two behaviours worth knowing about before changing anything:
 *
 * - **The card never re-renders while you are editing.** Input events mutate the
 *   working copy without touching the DOM, so a poll landing mid-edit cannot
 *   move the field under the cursor. It re-syncs from the entity only when
 *   nothing is unsaved.
 * - **Validation lives on the other side.** The action refuses a day that does
 *   not run forwards, a temperature the thermostat will not take, and so on,
 *   with a message written for a person. The card shows that message rather
 *   than trying to reimplement the rules and drift from them.
 */

const DAYS = [
  "sunday",
  "monday",
  "tuesday",
  "wednesday",
  "thursday",
  "friday",
  "saturday",
];
const LABELS = {
  sunday: "Sun",
  monday: "Mon",
  tuesday: "Tue",
  wednesday: "Wed",
  thursday: "Thu",
  friday: "Fri",
  saturday: "Sat",
};

// Register 29's legend, as the integration spells it. The grouping follows from
// it: in 5/2 and 24 hour mode the seven day blocks are not independent, so the
// card must not offer to edit them separately.
const GROUPS = {
  "24 hour": [{ key: "all", label: "Every day", days: DAYS }],
  "5/2 day": [
    {
      key: "weekdays",
      label: "Mon – Fri",
      days: ["monday", "tuesday", "wednesday", "thursday", "friday"],
    },
    { key: "weekend", label: "Sat – Sun", days: ["saturday", "sunday"] },
  ],
};

const STYLES = `
  :host { display: block; }
  .body { padding: 0 16px 16px; }
  .days {
    display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px;
  }
  .chip {
    border: 1px solid var(--divider-color, #e0e0e0);
    background: transparent;
    color: var(--primary-text-color);
    border-radius: 16px;
    padding: 5px 13px;
    font: inherit;
    font-size: 13px;
    cursor: pointer;
    transition: background-color .15s, border-color .15s;
  }
  .chip:hover { border-color: var(--primary-color); }
  .chip[aria-pressed="true"] {
    background: var(--primary-color);
    border-color: var(--primary-color);
    color: var(--text-primary-color, #fff);
  }
  table { width: 100%; border-collapse: collapse; }
  th {
    text-align: left;
    font-size: 12px;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: .05em;
    color: var(--secondary-text-color);
    padding: 0 8px 6px 0;
  }
  td { padding: 4px 8px 4px 0; }
  td:last-child, th:last-child { padding-right: 0; }
  tr.off input { opacity: .4; }
  .num { color: var(--secondary-text-color); width: 1em; font-variant-numeric: tabular-nums; }
  input[type="time"], input[type="number"] {
    font: inherit;
    color: var(--primary-text-color);
    background: var(--card-background-color, transparent);
    border: 1px solid var(--divider-color, #e0e0e0);
    border-radius: 6px;
    padding: 6px 8px;
    width: 100%;
    box-sizing: border-box;
    color-scheme: light dark;
  }
  input:focus-visible { outline: 2px solid var(--primary-color); outline-offset: -1px; }
  .toggle {
    border: none; background: transparent; cursor: pointer; padding: 4px;
    color: var(--secondary-text-color); font: inherit; font-size: 18px; line-height: 1;
  }
  .toggle:hover { color: var(--primary-color); }
  .footer {
    display: flex; align-items: center; gap: 12px;
    margin-top: 14px; min-height: 36px;
  }
  button.action {
    font: inherit; font-size: 14px; font-weight: 500;
    border-radius: 6px; padding: 8px 16px; cursor: pointer;
    border: 1px solid var(--primary-color);
    background: var(--primary-color); color: var(--text-primary-color, #fff);
  }
  button.action.secondary {
    background: transparent; color: var(--primary-color);
  }
  button.action[disabled] { opacity: .5; cursor: default; }
  .status { font-size: 13px; color: var(--secondary-text-color); flex: 1; }
  .status.error { color: var(--error-color, #db4437); }
  .note { color: var(--secondary-text-color); font-size: 13px; padding: 4px 0 12px; }
  .empty { padding: 16px; color: var(--secondary-text-color); }
  @media (max-width: 420px) {
    th:first-child, td.num { display: none; }
  }
`;

class HeatmiserEdgeScheduleCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._rows = null; // the working copy being edited
    this._group = null; // which day (or group of days) it came from
    this._dirty = false;
    this._status = "";
    this._error = false;
    this._built = false;
  }

  setConfig(config) {
    if (!config || !config.entity) {
      throw new Error("Set an entity: the thermostat's Weekly program sensor.");
    }
    if (!config.entity.startsWith("sensor.")) {
      throw new Error("The entity must be a Weekly program sensor.");
    }
    this._config = config;
    this._rows = null;
    this._built = false;
  }

  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find((id) =>
      isProgrammeSensor(hass.states[id], id),
    );
    return { entity: entity || "sensor.weekly_program" };
  }

  static getConfigElement() {
    return document.createElement("heatmiser-edge-schedule-card-editor");
  }

  getCardSize() {
    return 5;
  }

  set hass(hass) {
    this._hass = hass;
    // Re-syncing while there are unsaved edits would throw them away, and a
    // poll lands every scan interval - so an unlucky moment would silently
    // discard a half-finished change.
    if (!this._dirty) this._sync();
    this._render();
  }

  // ------------------------------------------------------------------
  // State
  // ------------------------------------------------------------------

  get _state() {
    return this._hass && this._config
      ? this._hass.states[this._config.entity]
      : undefined;
  }

  get _groups() {
    const mode = this._state?.attributes.program_mode;
    if (GROUPS[mode]) return GROUPS[mode];
    return DAYS.map((day) => ({ key: day, label: LABELS[day], days: [day] }));
  }

  /** Take a fresh working copy from the entity, if the selection still exists. */
  _sync() {
    const attributes = this._state?.attributes;
    if (!attributes?.schedule) {
      this._rows = null;
      return;
    }
    const groups = this._groups;
    let group = groups.find((candidate) => candidate.key === this._group?.key);
    if (!group) {
      // First render, or the thermostat's program mode changed under us and
      // the day that was selected is no longer separately editable.
      const today = DAYS[new Date().getDay()];
      group = groups.find((candidate) => candidate.days.includes(today)) || groups[0];
    }
    this._group = group;
    const source = attributes.schedule[group.days[0]] || [];
    this._rows = source
      .slice(0, attributes.periods)
      .map((row) => ({ ...row }));
  }

  _isTimer() {
    return Boolean(this._rows?.length && "on" in this._rows[0]);
  }

  /** True when the days in the group do not all hold the same programme. */
  _daysDiffer() {
    const schedule = this._state?.attributes.schedule;
    if (!schedule || !this._group || this._group.days.length < 2) return false;
    const first = JSON.stringify(schedule[this._group.days[0]]);
    return this._group.days.some((day) => JSON.stringify(schedule[day]) !== first);
  }

  // ------------------------------------------------------------------
  // Rendering
  // ------------------------------------------------------------------

  _render() {
    if (!this._built) {
      this.shadowRoot.innerHTML = `<style>${STYLES}</style><ha-card><div class="content"></div></ha-card>`;
      this._built = true;
    }
    const card = this.shadowRoot.querySelector("ha-card");
    const content = this.shadowRoot.querySelector(".content");
    const state = this._state;

    card.setAttribute(
      "header",
      this._config.title ??
        state?.attributes.friendly_name?.replace(/ Weekly program$/, "") ??
        "Weekly programme",
    );

    if (!state) {
      content.innerHTML = `<div class="empty">${this._config.entity} is not available.</div>`;
      return;
    }
    if (!this._rows) {
      content.innerHTML = `<div class="empty">Nothing has read this thermostat's
        programme yet. It is read once after Home Assistant starts, and after
        every change.</div>`;
      return;
    }
    // Non-programmable is shown, not hidden. The grid is still stored and still
    // readable - hardware 2026-08-13, a stat in this mode holds the manual's
    // full default programme - it is only the *writing* the thermostat refuses.
    // Blanking the card would throw away something true.
    const locked = state.attributes.program_mode === "Non-programmable";
    content.innerHTML = `<div class="body">
      <div class="days">${this._renderChips()}</div>
      ${
        locked
          ? `<div class="note">This thermostat's program mode is
             <b>Non-programmable</b>, so it does not run a weekly programme and
             will not accept a change to one. This is what it has stored.</div>`
          : ""
      }
      ${this._daysDiffer() && !locked ? '<div class="note">These days do not currently match. Saving will make them all match what is shown.</div>' : ""}
      <table>${this._renderHead()}<tbody>${this._renderRows(locked)}</tbody></table>
      ${
        locked
          ? ""
          : `<div class="footer">
        <button class="action" data-act="save"${this._dirty ? "" : " disabled"}>Save</button>
        <button class="action secondary" data-act="revert"${this._dirty ? "" : " disabled"}>Revert</button>
        <span class="status${this._error ? " error" : ""}">${escapeHtml(this._status)}</span>
      </div>`
      }
    </div>`;
    this._wire(content, locked);
  }

  _renderChips() {
    return this._groups
      .map(
        (group) =>
          `<button class="chip" role="button" aria-pressed="${
            group.key === this._group.key
          }" data-group="${group.key}">${group.label}</button>`,
      )
      .join("");
  }

  _renderHead() {
    const columns = this._isTimer()
      ? "<th>On</th><th>Off</th>"
      : `<th>From</th><th>Temperature</th>`;
    return `<thead><tr><th>#</th>${columns}<th></th></tr></thead>`;
  }

  _renderRows(locked = false) {
    const unit = this._state.attributes.temperature_unit || "°C";
    const [low, high, step] = unit === "°F" ? [41, 95, 1] : [5, 35, 0.5];
    return this._rows
      .map((row, index) => {
        const off = this._isTimer() ? row.on === null : row.time === null;
        const dead = off || locked ? " disabled" : "";
        const cells = this._isTimer()
          ? `<td>${timeInput(index, "on", row.on, locked)}</td>
             <td>${timeInput(index, "off", row.off, locked)}</td>`
          : `<td>${timeInput(index, "time", row.time, locked)}</td>
             <td><input type="number" data-row="${index}" data-field="temperature"
                  min="${low}" max="${high}" step="${step}"
                  value="${row.temperature ?? ""}"${dead}></td>`;
        return `<tr class="${off ? "off" : ""}">
          <td class="num">${row.period}</td>
          ${cells}
          <td>${
            locked
              ? ""
              : `<button class="toggle" data-row="${index}" data-act="toggle"
               title="${off ? "Switch this period on" : "Switch this period off"}"
               >${off ? "＋" : "×"}</button>`
          }</td>
        </tr>`;
      })
      .join("");
  }

  _wire(root, locked = false) {
    // The day chips stay live even when locked: looking at Tuesday is reading,
    // not writing.
    if (locked) {
      this._wireChips(root);
      return;
    }
    this._wireChips(root);

    // Input events deliberately do not re-render: rebuilding the table under a
    // half-typed time is how a card ends up fighting its user.
    root.querySelectorAll("input").forEach((input) => {
      input.addEventListener("change", () => {
        const row = this._rows[Number(input.dataset.row)];
        const field = input.dataset.field;
        if (field === "temperature") {
          row.temperature = input.value === "" ? null : Number(input.value);
        } else {
          row[field] = input.value || null;
        }
        this._markDirty(root);
      });
    });

    root.querySelectorAll('[data-act="toggle"]').forEach((button) => {
      button.addEventListener("click", () => {
        const row = this._rows[Number(button.dataset.row)];
        if (this._isTimer()) {
          const on = row.on === null;
          row.on = on ? "07:00" : null;
          row.off = on ? "09:00" : null;
        } else {
          row.time = row.time === null ? "07:00" : null;
        }
        this._dirty = true;
        this._status = "";
        this._render(); // a whole row changes shape, so this one does redraw
      });
    });

    root.querySelector('[data-act="save"]')?.addEventListener("click", () => this._save());
    root.querySelector('[data-act="revert"]')?.addEventListener("click", () => {
      this._dirty = false;
      this._status = "";
      this._sync();
      this._render();
    });
  }

  _wireChips(root) {
    root.querySelectorAll("[data-group]").forEach((chip) => {
      chip.addEventListener("click", () => {
        if (chip.dataset.group === this._group.key) return;
        this._group = this._groups.find((g) => g.key === chip.dataset.group);
        // Switching day discards unsaved edits. Keeping them would silently
        // copy one day's changes onto another, which nobody asked for.
        this._dirty = false;
        this._status = "";
        this._sync();
        this._render();
      });
    });
  }

  _markDirty(root) {
    if (this._dirty) return;
    this._dirty = true;
    // Enable the buttons without a re-render, so focus stays where it is.
    root.querySelectorAll("button.action").forEach((b) => b.removeAttribute("disabled"));
  }

  // ------------------------------------------------------------------
  // Saving
  // ------------------------------------------------------------------

  async _save() {
    this._status = "Saving…";
    this._error = false;
    this._render();

    const periods = this._rows.map((row) =>
      this._isTimer()
        ? { period: row.period, on: row.on, off: row.off }
        : {
            period: row.period,
            time: row.time,
            // Omitted when the period is off, so the thermostat keeps the
            // temperature it already holds - the same bargain the action makes.
            ...(row.time === null ? {} : { temperature: row.temperature }),
          },
    );

    try {
      await this._hass.callService(
        "heatmiser_edge",
        "set_schedule",
        // The group's own key: "weekdays" and "all" are what the action
        // understands, and letting it do the expanding keeps one rule in one
        // place.
        { days: [this._group.key], periods },
        { entity_id: this._config.entity },
      );
      this._dirty = false;
      this._status = "Saved.";
      this._error = false;
      // The action re-reads the thermostat to verify, so the entity is about to
      // update on its own. Clearing the working copy lets that land.
      this._sync();
    } catch (err) {
      this._error = true;
      this._status = err?.message || "The thermostat did not take the change.";
    }
    this._render();
  }
}

/**
 * The visual editor: one entity picker, and nothing else.
 *
 * **Why `ha-entity-picker` has to be coaxed into existing.** The frontend loads
 * its editor elements lazily, so on a fresh page `ha-entity-picker` is simply
 * not defined and `whenDefined` would wait for ever. Asking the built-in
 * entities card for *its* config element is the standard way to make the
 * frontend load that bundle - it is a side effect, not a use of the card. If it
 * is still missing after that, the editor falls back to a plain text field
 * rather than rendering nothing, because a blank editor gives a user no way
 * back to a working card.
 */
class HeatmiserEdgeScheduleCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
  }

  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  connectedCallback() {
    this._loadPicker();
    this._render();
  }

  async _loadPicker() {
    if (customElements.get("ha-entity-picker") || this._loading) return;
    this._loading = true;
    try {
      const helpers = await window.loadCardHelpers();
      const card = await helpers.createCardElement({ type: "entities", entities: [] });
      await card.constructor.getConfigElement();
    } catch (err) {
      // Falls through to the text-field fallback below.
    }
    this._built = false;
    this._render();
  }

  _render() {
    if (!this._hass) return;
    const havePicker = Boolean(customElements.get("ha-entity-picker"));
    if (!this._built) {
      this.shadowRoot.innerHTML = `<style>
        .form { display: block; padding: 8px 0; }
        .fallback {
          font: inherit; width: 100%; box-sizing: border-box; padding: 8px;
          color: var(--primary-text-color);
          background: var(--card-background-color, transparent);
          border: 1px solid var(--divider-color, #e0e0e0); border-radius: 6px;
        }
        .hint { color: var(--secondary-text-color); font-size: 12px; padding-top: 6px; }
      </style><div class="form"></div>`;
      const form = this.shadowRoot.querySelector(".form");
      this._field = document.createElement(
        havePicker ? "ha-entity-picker" : "input",
      );
      if (havePicker) {
        this._field.label = "Weekly program sensor";
        this._field.includeDomains = ["sensor"];
        // Every Heatmiser EDGE thermostat has exactly one of these, and nothing
        // else in Home Assistant looks like it - so the picker can show the
        // right handful rather than every sensor in the house.
        this._field.entityFilter = (value) =>
          isProgrammeSensor(
            typeof value === "string" ? this._hass.states[value] : value,
            typeof value === "string" ? value : value?.entity_id,
          );
        this._field.addEventListener("value-changed", (event) => {
          event.stopPropagation();
          this._emit(event.detail.value);
        });
      } else {
        this._field.className = "fallback";
        this._field.placeholder = "sensor.example_weekly_program";
        this._field.addEventListener("change", () => this._emit(this._field.value));
        const hint = document.createElement("div");
        hint.className = "hint";
        hint.textContent =
          "Pick the thermostat's Weekly program sensor, for example sensor.hall_weekly_program.";
        form.append(this._field, hint);
        this._built = true;
        return;
      }
      form.append(this._field);
      this._built = true;
    }
    this._field.hass = this._hass;
    this._field.value = this._config.entity || "";
  }

  _emit(entity) {
    if (entity === this._config.entity) return;
    this._config = { ...this._config, entity };
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: this._config },
        bubbles: true,
        composed: true,
      }),
    );
  }
}

/** The one entity this card can read: a Heatmiser EDGE weekly programme. */
function isProgrammeSensor(state, entityId) {
  return Boolean(
    state &&
      (entityId ?? state.entity_id ?? "").startsWith("sensor.") &&
      state.attributes &&
      state.attributes.schedule &&
      state.attributes.periods,
  );
}

function timeInput(index, field, value, locked = false) {
  return `<input type="time" data-row="${index}" data-field="${field}"
    value="${value ?? ""}"${value === null || locked ? " disabled" : ""}>`;
}

function escapeHtml(text) {
  const node = document.createElement("div");
  node.textContent = text ?? "";
  return node.innerHTML;
}

customElements.define("heatmiser-edge-schedule-card", HeatmiserEdgeScheduleCard);
customElements.define(
  "heatmiser-edge-schedule-card-editor",
  HeatmiserEdgeScheduleCardEditor,
);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "heatmiser-edge-schedule-card",
  name: "Heatmiser EDGE Schedule",
  description: "View and edit a Heatmiser EDGE thermostat's weekly programme.",
  // The card picker renders a live preview from `getStubConfig`, which is worth
  // having: it lands on a real thermostat if there is one.
  preview: true,
  documentationURL: "https://github.com/dklemm/home-assistant-heatmiser-edge",
});
