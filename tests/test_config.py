from pathlib import Path

from fb_group_downloader.config import AppConfig, GroupConfig, load_or_create_config


def test_default_config(tmp_path: Path):
    cfg_file = tmp_path / "config.yaml"
    cfg = load_or_create_config(cfg_file)
    assert len(cfg.groups) == 1
    assert cfg.groups[0].group_id == "example_group_id"
    assert cfg.schedule.interval_minutes == 60
    assert cfg.headless is True


def test_save_and_load_config(tmp_path: Path):
    cfg_file = tmp_path / "test_config.yaml"
    cfg = AppConfig(
        storage_dir=Path("./my_downloads"),
        headless=False,
        groups=[
            GroupConfig(
                group_id="test_group_123",
                name="測試社團",
                download_images=True,
                download_videos=False,
                download_files=True,
            )
        ],
    )
    cfg.save_to_yaml(cfg_file)

    loaded = AppConfig.load_from_yaml(cfg_file)
    assert loaded.headless is False
    assert len(loaded.groups) == 1
    assert loaded.groups[0].group_id == "test_group_123"
    assert loaded.groups[0].download_videos is False
