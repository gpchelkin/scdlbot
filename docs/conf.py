"""Sphinx settings for https://scdlbot.readthedocs.io. The pages include the Markdown files from the repository root."""

project = "scdlbot"
author = "George Pchelkin"
copyright = "2016, George Pchelkin"
extensions = ["myst_parser"]
myst_heading_anchors = 3  # the README links to its own sections
html_theme = "furo"
html_title = "scdlbot"
exclude_patterns = ["_build"]
