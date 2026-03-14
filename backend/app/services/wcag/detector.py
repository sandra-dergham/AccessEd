from .batch1_rules import run_batch1_rules
#from .batch2_rules import run_batch2_rules
from .batch3_rules import run_batch3_rules

def run_wcag_detector(document_model: dict) -> list[dict]:
    issues: list[dict] = []
    issues += run_batch1_rules(document_model)
    #issues += run_batch2_rules(document_model)
    issues += run_batch3_rules(document_model)
    return issues