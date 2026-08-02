#!/usr/bin/env python3
from pathlib import Path
p=Path('tools/apply_unified_subscription_patch.py')
s=p.read_text(encoding='utf-8')
s=s.replace("r'^ask_center_parameters\\(\\) \\{.*?^\\}\\n'", "r'^ask_center_parameters\\(\\)\\s*\\{.*?^\\}\\n'")
s=s.replace("r'^show_parameter_summary\\(\\) \\{.*?^\\}\\n'", "r'^show_parameter_summary\\(\\)\\s*\\{.*?^\\}\\n'")
p.write_text(s,encoding='utf-8')
