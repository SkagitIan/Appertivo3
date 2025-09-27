from django import template

register = template.Library()


@register.filter
def dict_lookup(mapping, key):
    """Return a dictionary value for the given key, handling string conversions."""

    if not isinstance(mapping, dict):
        return None
    if key in mapping:
        return mapping[key]
    if key is None:
        return None
    key_str = str(key)
    return mapping.get(key_str)
