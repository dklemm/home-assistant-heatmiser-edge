"""Things hassfest and HACS would fail on, caught before CI does.

Cheap to run, and they catch the class of mistake that is invisible in review:
a translation copy that drifted, a string key with no matching step, a domain
that agrees with itself in three places but not a fourth.
"""

import json
import re
from pathlib import Path

import yaml

from custom_components.heatmiser_edge.const import DOMAIN, MODEL_LABELS, MODELS

ROOT = Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "heatmiser_edge"


def load(path: Path):
    return json.loads(path.read_text())


def test_translations_are_a_byte_for_byte_copy_of_strings():
    """`translations/en.json` is a copy, not a fork. Editing one and not the
    other means the UI and the source of truth disagree.
    """
    strings = (COMPONENT / "strings.json").read_bytes()
    english = (COMPONENT / "translations" / "en.json").read_bytes()
    assert strings == english, (
        "run: cp custom_components/heatmiser_edge/strings.json "
        "custom_components/heatmiser_edge/translations/en.json"
    )


def test_the_domain_agrees_everywhere():
    manifest = load(COMPONENT / "manifest.json")
    assert manifest["domain"] == DOMAIN
    assert COMPONENT.name == DOMAIN
    assert load(ROOT / "hacs.json")["name"] == manifest["name"]


def test_the_manifest_declares_what_it_needs():
    manifest = load(COMPONENT / "manifest.json")
    assert manifest["config_flow"] is True
    assert manifest["iot_class"] == "local_polling"
    assert manifest["integration_type"] == "hub"
    requirements = " ".join(manifest["requirements"])
    assert "pymodbus" in requirements
    # pymodbus imports pyserial lazily and raises if it is missing, so the
    # serial transport needs it declared even though pymodbus does not depend
    # on it. Home Assistant happening to ship it is not something to rely on.
    assert "pyserial" in requirements


def test_every_config_step_has_strings():
    """A step with no strings renders as a blank form with raw field keys."""
    from custom_components.heatmiser_edge.config_flow import EdgeConfigFlow

    strings = load(COMPONENT / "strings.json")
    steps = set(strings["config"]["step"])
    # Only our own steps: the base class contributes every discovery method
    # (zeroconf, ssdp, bluetooth...) that this integration never uses.
    declared = {
        name.removeprefix("async_step_")
        for name in vars(EdgeConfigFlow)
        if name.startswith("async_step_")
    }
    # `scan` is a progress step and `failed` only aborts; neither shows a form.
    assert declared - steps == {"scan", "failed"}


def test_every_abort_and_error_reason_has_a_string():
    from custom_components.heatmiser_edge import config_flow

    strings = load(COMPONENT / "strings.json")
    source = (COMPONENT / "config_flow.py").read_text()
    for reason in ("no_thermostats_found", "cannot_connect", "invalid_unit_ids"):
        assert reason in source
    aborts = strings["config"]["abort"]
    errors = strings["config"]["error"]
    assert "no_thermostats_found" in aborts
    assert "cannot_connect" in aborts
    assert "already_configured" in aborts
    assert "reconfigure_successful" in aborts
    assert "invalid_unit_ids" in errors
    assert config_flow.DOMAIN == DOMAIN


def test_the_progress_action_is_named():
    """Without this the progress dialog shows an untranslated key."""
    strings = load(COMPONENT / "strings.json")
    assert "scanning" in strings["config"]["progress"]


def test_model_labels_cover_every_model():
    assert set(MODEL_LABELS) == set(MODELS)


def test_every_action_and_field_has_strings():
    """An action with no strings shows the raw key in the UI, and hassfest fails.

    Also checks the other direction: a field renamed in `services.yaml` and not
    in `strings.json` leaves an orphan string that looks translated and is not.
    """
    services = yaml.safe_load((COMPONENT / "services.yaml").read_text())
    strings = load(COMPONENT / "strings.json")["services"]
    assert set(services) == set(strings)
    for name, service in services.items():
        assert strings[name]["name"] and strings[name]["description"]
        fields = set(service.get("fields", {}))
        assert fields == set(strings[name].get("fields", {}))
        for field in fields:
            described = strings[name]["fields"][field]
            assert described["name"] and described["description"]


def test_no_action_filters_its_target_by_device():
    """hassfest refuses any `device` key under an action's `target`.

    `raise_on_target_device_filter` in `script/hassfest/services.py` tests
    `if "device" in value`, so nesting it under `filter:` does not help either.
    The obvious `device: integration: heatmiser_edge` is what anyone adding a
    fifth action writes first, and without this it costs a full CI round trip
    to find out. See the `set_time` section of CLAUDE.md for why neither way
    round it is usable.
    """
    services = yaml.safe_load((COMPONENT / "services.yaml").read_text())
    for name, service in services.items():
        assert "target" in service, f"{name} is targeted and needs a target block"
        assert "device" not in (service["target"] or {}), (
            f"{name}: hassfest refuses a device filter on target"
        )


def test_every_raised_translation_key_has_a_message():
    """A `translation_key` with no string surfaces as the bare key in a toast.

    `schedule.py` counts too: its `ScheduleError` keys are handed straight to
    `ServiceValidationError` by `services.py`, so a new one with no string is
    the same bug arriving by a different route.
    """
    strings = load(COMPONENT / "strings.json")["exceptions"]
    raised = set(
        re.findall(r'translation_key="([^"]+)"', (COMPONENT / "services.py").read_text())
    )
    raised |= set(
        re.findall(r'ScheduleError\(\s*"([^"]+)"', (COMPONENT / "schedule.py").read_text())
    )
    assert raised
    assert raised <= set(strings), sorted(raised - set(strings))
    for key in raised:
        assert strings[key]["message"]


def test_the_action_the_service_module_registers_is_the_one_documented():
    """`services.yaml` is what the UI reads; `hass.services` is what runs."""
    from custom_components.heatmiser_edge.const import SERVICE_SET_TIME

    services = yaml.safe_load((COMPONENT / "services.yaml").read_text())
    assert SERVICE_SET_TIME in services


def test_every_platform_module_exists():
    from custom_components.heatmiser_edge import PLATFORMS

    for platform in PLATFORMS:
        assert (COMPONENT / f"{platform.value}.py").is_file()


def test_the_card_is_where_the_integration_serves_it_from():
    """`__init__.py` registers one static path. A rename on either side gives a
    404 and a card that silently never loads.
    """
    from custom_components.heatmiser_edge import CARD_FILENAME

    card = COMPONENT / "www" / CARD_FILENAME
    assert card.is_file()
    source = card.read_text()
    # The element name is what a dashboard's `type: custom:...` refers to, so
    # the two places it appears must agree.
    name = CARD_FILENAME.removesuffix(".js")
    assert f'customElements.define("{name}"' in source
    assert f'type: "{name}"' in source
    # The visual editor is found by convention: `getConfigElement` creates the
    # element, and a name that does not match a registered one leaves the UI
    # editor blank with no error anywhere.
    assert f'createElement("{name}-editor")' in source
    assert f'"{name}-editor",' in source


def test_the_card_and_the_integration_agree_on_the_grid():
    """The card hardcodes day names, mode labels and attribute keys.

    All three come from Python, and none of them is checked by anything the
    browser runs - so a label edited in `const.py` would leave the card quietly
    offering the wrong day grouping, or none at all.
    """
    from custom_components.heatmiser_edge import CARD_FILENAME
    from custom_components.heatmiser_edge.const import PROGRAM_MODE_LABELS
    from custom_components.heatmiser_edge.schedule import DAYS

    source = (COMPONENT / "www" / CARD_FILENAME).read_text()

    days = re.search(r"const DAYS = \[(.*?)\];", source, re.S)
    assert tuple(re.findall(r'"(\w+)"', days.group(1))) == DAYS

    # Every mode the card names must be one the integration can report, and the
    # two that group the week must both be handled.
    named = set(re.findall(r'"(5/2 day|7 day|24 hour|Non-programmable)"', source))
    assert named <= set(PROGRAM_MODE_LABELS.values())
    assert {"5/2 day", "24 hour", "Non-programmable"} <= named

    for attribute in ("schedule", "periods", "program_mode", "temperature_unit"):
        assert f"attributes.{attribute}" in source
