from enum import Enum

class SimplePromptTemplate(str, Enum):
    # Asks for the same tagged verdict the reasoning prompt does: free prose
    # cannot be read reliably, since a hedged "No, though arguably yes..."
    # contains the word yes.

    # "or any part of it" is doing real work. A requirement usually names
    # several capabilities and an element delivers one of them, so asking
    # whether it implements *the requirement* invites a literal reader to say
    # no: login() does not, on its own, implement "create an account, log in,
    # update profiles and reset passwords". Without this, a whole run of
    # method-level classification came back empty.
    DEFAULT = (
        "{source_type}: '''{source_content}'''\n"
        "{target_type}: '''{target_content}'''\n"
        "Does this {target_type} implement the {source_type}, or any part of it?\n"
        "Reply with exactly one of <trace>yes</trace> or <trace>no</trace>. "
        "Output nothing else - no reasoning, no explanation, no punctuation."
    )

class ReasoningPromptTemplate(str, Enum): 
    DEFAULT_SYSTEM = (
        "You are an expert in software traceability. "
        "Your task is to determine whether a source artifact and a target artifact are related. "
        "Always begin your reply with exactly one of <trace>yes</trace> or <trace>no</trace>. "
        "If related, follow it with one sentence inside "
        "<explanation>your explanation</explanation>. "
        "Put no text outside these tags."
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