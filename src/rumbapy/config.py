from __future__ import annotations

import os
import configparser
from typing import Optional, Union
from dataclasses import dataclass
from pathlib import Path

@dataclass
class CredentialsConfig:
    cics_id: str
    cics_password: str
    ims_id: str
    ims_password: str

    @classmethod
    def from_config(cls, config: configparser.ConfigParser) -> CredentialsConfig:
        return cls(
            cics_id=config['cics']['id'],
            cics_password=config['cics']['password'],
            ims_id=config['rhelp']['id'],
            ims_password=config['rhelp']['password']
        )

@dataclass
class PathsConfig:
    python_32bit: Path

    @classmethod
    def from_config(cls, config: configparser.ConfigParser) -> PathsConfig:
        return cls(
            python_32bit=Path(config['32bit_python']['path'])
        )

class Config:
    _config_instance = None
    
    def __init__(self, config_path: Optional[Union[str, Path]] = None) -> None:
        self.config_path = config_path or Path("config.ini")
        self.config = self._load_config()

        self.credentials = CredentialsConfig.from_config(self.config)
        self.paths = PathsConfig.from_config(self.config)

    def _load_config(self) -> configparser.ConfigParser:
        if not os.path.exists(self.config_path):
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        config = configparser.ConfigParser()
        config.read(self.config_path, encoding='utf-8')
        return config
    
    @classmethod
    def load_config(cls, config_path: Optional[Union[str, Path]] = None) -> configparser.ConfigParser:
        if cls._config_instance is None:
            cls._config_instance = cls(config_path)
        return cls._config_instance.config
