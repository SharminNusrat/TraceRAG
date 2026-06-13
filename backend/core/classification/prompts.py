from enum import Enum

class SimplePromptTemplate(str, Enum):
    DEFAULT = (
        "{source_type}: '''{source_content}'''\n"
        "{target_type}: '''{target_content}'''\n"
        "Are they related? Answer with 'yes' or 'no'."
    )

class ReasoningPromptTemplate(str, Enum): 
    DEFAULT_SYSTEM = (
        "You are an expert in software traceability. "
        "Your task is to determine whether a source artifact and a target artifact are related. "
        "Answer with <trace>yes</trace> or <trace>no</trace>. "
        "If related, explain why in one sentence inside <explanation>your explanation</explanation>."
    )

    DEFAULT_USER = (
        "{source_type}: '''{source_content}'''\n"
        "{target_type}: '''{target_content}'''\n"
        "Are they related?"
    )

def format_prompt(template: str, source_type: str, source_content: str, target_type: str, target_content: str) -> str:
    return (
        template
        .replace("{source_type}", source_type)
        .replace("{source_content}", source_content)
        .replace("{target_type}", target_type)
        .replace("{target_content}", target_content)
    )