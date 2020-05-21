"""Module to store jinja2 templates for ccpy"""
from os.path import dirname, abspath, split
import logging
from jinja2 import Environment, FileSystemLoader, Template

LOG = logging.getLogger(__name__)
CCPY_TEMPLATE_DIR = dirname(abspath(__file__))
CCPY_TEMPLATE_ENV = Environment(loader=FileSystemLoader(CCPY_TEMPLATE_DIR))


# Object to store all templates
_ALL_TEMPLATES = {
    "turbomole": CCPY_TEMPLATE_ENV.get_template("tmol.jinja2"),
    "crystal17": CCPY_TEMPLATE_ENV.get_template("crystal17.jinja2"),
}


def add_template(text=None, filename=None, name="new_template"):
    if filename:
        path, filename = split(filename)
        _ALL_TEMPLATES[name] = Environment(
            loader=FileSystemLoader(path or "./")
        ).get_template(filename)
    elif text:
        _ALL_TEMPLATES[name] = Template(text)
    return _ALL_TEMPLATES[name]


def load_template(name):
    result = _ALL_TEMPLATES.get(name)
    if result is None:
        try:
            return CCPY_TEMPLATE_ENV.get_template(name)
        except Exception as e:
            LOG.error("Could not find template: %s (%s)", name, e)
    return result