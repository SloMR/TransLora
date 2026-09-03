from core.providers import (
    CUSTOM,
    PROVIDER_CHOICES,
    PROVIDER_PRESETS,
    ModelOption,
    price_label,
)


def test_every_preset_lists_its_cheapest_model_first_and_defaults_to_it():
    for key, preset in PROVIDER_PRESETS.items():
        costs = [m.input + m.output for m in preset.models]
        assert costs == sorted(costs), key
        assert preset.default_model == (preset.models[0].id if preset.models else "")


def test_the_custom_preset_is_an_empty_endpoint_and_not_a_flag_choice():
    custom = PROVIDER_PRESETS[CUSTOM]
    assert custom.api_url == "" and custom.models == () and not custom.needs_key
    assert CUSTOM not in PROVIDER_CHOICES
    assert set(PROVIDER_CHOICES) == set(PROVIDER_PRESETS) - {CUSTOM}


def test_the_price_label_keeps_three_decimals_only_under_ten_cents():
    assert price_label(ModelOption("x", 0.075, 0.3, "")) == "$0.075 in, $0.30 out per 1M tokens"
    assert price_label(ModelOption("x", 2, 10, "")) == "$2.00 in, $10.00 out per 1M tokens"
