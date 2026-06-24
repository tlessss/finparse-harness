
import yaml
from .base_engine import FinanceParserEngine

class RuleHotLoader:
    @staticmethod
    def load_rule(rule_path: str):
        # 加载AI最新修改/新建的规则
        with open(rule_path, "r", encoding="utf-8") as f:
            rule_config = yaml.safe_load(f)
        # 注入引擎，热生效，无需重启服务
        return FinanceParserEngine(rule=rule_config)

    @staticmethod
    def save_new_rule(rule_path: str, new_rule: dict):
        # AI生成新规则后自动落地文件（新建解析器核心）
        with open(rule_path, "w", encoding="utf-8") as f:
            yaml.dump(new_rule, f, allow_unicode=True, sort_keys=False)