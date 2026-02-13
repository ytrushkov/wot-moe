"""Tests for configuration loading."""

from pathlib import Path

from tankvision.config import DEFAULTS, load_config


class TestLoadConfig:
    def test_missing_file_returns_defaults(self, tmp_path: Path):
        config = load_config(tmp_path / "nonexistent.toml")
        assert config["ocr"]["sample_rate"] == DEFAULTS["ocr"]["sample_rate"]
        assert config["server"]["ws_port"] == DEFAULTS["server"]["ws_port"]

    def test_partial_override(self, tmp_path: Path):
        config_file = tmp_path / "config.toml"
        config_file.write_text('[player]\ngamertag = "TestPlayer"\n')
        config = load_config(config_file)
        assert config["player"]["gamertag"] == "TestPlayer"
        # Unspecified values should use defaults
        assert config["player"]["platform"] == "xbox"
        assert config["ocr"]["sample_rate"] == 2

    def test_full_override(self, tmp_path: Path):
        config_file = tmp_path / "config.toml"
        config_file.write_text(
            '[server]\nhttp_port = 8080\nws_port = 8081\n'
        )
        config = load_config(config_file)
        assert config["server"]["http_port"] == 8080
        assert config["server"]["ws_port"] == 8081

    def test_defaults_not_mutated(self, tmp_path: Path):
        """Loading config should not mutate the DEFAULTS dict."""
        original_rate = DEFAULTS["ocr"]["sample_rate"]
        config_file = tmp_path / "config.toml"
        config_file.write_text('[ocr]\nsample_rate = 10\n')
        config = load_config(config_file)
        assert config["ocr"]["sample_rate"] == 10
        assert DEFAULTS["ocr"]["sample_rate"] == original_rate
