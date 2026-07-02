"""轻量模板渲染：{{var}} 占位符替换。"""

import re
from typing import Any, Dict

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")


def render_template(text: str, variables: Dict[str, Any]) -> str:
    """把模板里的 {{key}} 替换成 variables[key]；缺失键保留原样。"""

    def _sub(m: re.Match) -> str:
        key = m.group(1)
        if key not in variables:
            return m.group(0)
        val = variables[key]
        if val is None:
            return ""
        if isinstance(val, (list, tuple)):
            return "\n".join(str(x) for x in val)
        return str(val)

    return _PLACEHOLDER.sub(_sub, text or "")
