#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# @Author: José Sánchez-Gallego (gallegoj@uw.edu)
# @Date: 2018-09-16
# @Filename: conf.py
# @License: BSD 3-clause (http://www.opensource.org/licenses/BSD-3-Clause)

# type: ignore

import os

from pkg_resources import parse_version

from jaeger import __version__


# Are we building in RTD?
on_rtd = os.environ.get("READTHEDOCS") == "True"


# matplotlib.use('agg')


extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.autosummary",
    "sphinx.ext.todo",
    "sphinx.ext.viewcode",
    "sphinx.ext.mathjax",
    "sphinx.ext.intersphinx",
    "sphinx_click.ext",
    "sphinx-jsonschema",
    "myst_parser",
    "sphinx_copybutton",
]

# Add any paths that contain templates here, relative to this directory.
templates_path = ["_templates"]

# The suffix(es) of source filenames.
# You can specify multiple suffix as a list of string:
# source_suffix = ['.rst', '.md']
source_suffix = ".rst"

source_parsers = {
    # '.md': 'recommonmark.parser.CommonMarkParser',
}

# The master toctree document.
master_doc = "index"

# General information about the project.
project = project = "jaeger"
copyright = "{0}, {1}".format("2018-2020", "José Sánchez-Gallego")
author = "José Sánchez-Gallego"

# The version info for the project you're documenting, acts as replacement for
# |version| and |release|, also used in various other places throughout the
# built documents.

# The short X.Y version.
version = parse_version(__version__).base_version
# The full version, including alpha/beta/rc tags.
release = __version__

# The language for content autogenerated by Sphinx. Refer to documentation
# for a list of supported languages.
#
# This is also used if you do content translation via gettext catalogs.
# Usually you set "language" from the command line for these cases.
language = None

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This patterns also effect to html_static_path and html_extra_path
exclude_patterns = [
    "_build",
    "Thumbs.db",
    ".DS_Store",
]

# The reST default role (used for this markup: `text`) to use for all
# documents.
default_role = "py:obj"

# If true, '()' will be appended to :func: etc. cross-reference text.
# add_function_parentheses = True

# If true, the current module name will be prepended to all description
# unit titles (such as .. function::).
add_module_names = True

# If true, sectionauthor and moduleauthor directives will be shown in the
# output. They are ignored by default.
# show_authors = False

# The name of the Pygments (syntax highlighting) style to use.
pygments_style = "sphinx"

# A list of ignored prefixes for module index sorting.
# modindex_common_prefix = []

# If true, keep warnings as "system message" paragraphs in the built documents.
# keep_warnings = False

# If true, `todo` and `todoList` produce output, else they produce nothing.
todo_include_todos = False

# Intersphinx mappings
intersphinx_mapping = {
    "python": ("https://docs.python.org/3.7", None),
    "numpy": ("http://docs.scipy.org/doc/numpy/", None),
    "drift": ("https://sdss-drift.readthedocs.io/en/latest/", None),
}
# 'astropy': ('http://docs.astropy.org/en/latest', None),
# 'matplotlib': ('https://matplotlib.org/', None),
# 'scipy': ('https://docs.scipy.org/doc/scipy/reference', None)}

# autodoc_typehints = "description"

autodoc_mock_imports = ["_tkinter", "fps_calibrations"]
autodoc_member_order = "groupwise"
autodoc_default_options = {"members": None, "show-inheritance": None}
autodoc_typehints = "description"

napoleon_use_rtype = False
napoleon_use_ivar = True

copybutton_prompt_text = r">>> |\$ "
copybutton_prompt_is_regexp = True

rst_epilog = f"""
.. |jaeger_version| replace:: {__version__}
"""


# -- Options for HTML output ----------------------------------------------

# The theme to use for HTML and HTML Help pages.  See the documentation for
# a list of builtin themes.
html_theme = "furo"
html_title = "jaeger"
html_logo = "_static/sdssv_logo.png"
html_favicon = "./_static/favicon.ico"

# Add any paths that contain custom static files (such as style sheets) here,
# relative to this directory. They are copied after the builtin static files,
# so a file named "default.css" will overwrite the builtin "default.css".

# See https://github.com/rtfd/readthedocs.org/issues/1776 for why we do this
if on_rtd:
    html_static_path = []
else:
    html_static_path = ["_static"]
