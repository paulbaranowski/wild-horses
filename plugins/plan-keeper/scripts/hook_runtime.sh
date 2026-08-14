#!/bin/bash
# Shared PreToolUse adapter for the three harnesses that run these hooks:
# Claude Code, Cursor, and Grok Build. Each one sends a different payload. Each
# one expects a different decision shape. So every allow-script reads its input
# and writes its answer through this file.
#
# VENDORED. An installed plugin ships alone, so it cannot source a file from a
# sibling plugin. Every plugin that runs an allow-script therefore carries its
# own byte-identical copy, and tests/test_vendored_hook_runtime.py fails if the
# copies drift. Edit one copy and you must re-copy it to all of them.
#
# Payload differences this file absorbs:
#
#   harness  event field      command field         decision shape
#   -------  ----------       -------------         --------------
#   Claude   hook_event_name  tool_input.command    hookSpecificOutput{...}
#   Cursor   hook_event_name  tool_input.command    {permission, agent_message}
#   Grok     hookEventName    toolInput.command     {decision, reason}
#
# Grok is not a Cursor variant. Its binary contains no `hookSpecificOutput`,
# no `permissionDecision`, and no `hook_event_name`. Its decision struct holds
# exactly two fields, `decision` and `reason`. Send it either of the other two
# shapes and it logs invalid output. Grok then fails open. The auto-approve
# stops working, and the prompts come back.

# Populate HOOK_RUNTIME and HOOK_COMMAND from a raw payload.
#
# HOOK_RUNTIME is claude, cursor, grok, or unknown. Detection reads a field
# that the harness in question actually sends. No harness is the fall-through.
# An unrecognised payload yields `unknown`, and an `unknown` runtime emits
# nothing. Silence drops the caller back to the normal allow-list and
# classifier flow. Guessing a shape would answer one harness in another
# harness's dialect.
hook_runtime_init() {
    local payload="$1"
    local snake camel

    snake=$(printf '%s' "$payload" | jq -r '.hook_event_name // empty')
    camel=$(printf '%s' "$payload" | jq -r '.hookEventName // empty')

    if [[ -n "$camel" ]]; then
        HOOK_RUNTIME=grok
    elif [[ "$snake" == "preToolUse" ]]; then
        HOOK_RUNTIME=cursor
    elif [[ -n "$snake" ]]; then
        HOOK_RUNTIME=claude
    else
        HOOK_RUNTIME=unknown
    fi

    HOOK_COMMAND=$(printf '%s' "$payload" |
        jq -r '.tool_input.command // .toolInput.command // empty')
}

# Approve the pending tool call, in the dialect of the detected harness.
#
# jq builds the JSON so an apostrophe or a quote in the reason cannot break out
# of the string. Key order matches what these harnesses were sent before this
# file existed. So the bytes are unchanged for Claude and Cursor.
hook_runtime_emit_allow() {
    local reason="$1"

    case "$HOOK_RUNTIME" in
        grok)
            jq -cn --arg r "$reason" '{decision: "allow", reason: $r}'
            ;;
        cursor)
            jq -cn --arg r "$reason" '{permission: "allow", agent_message: $r}'
            ;;
        claude)
            jq -cn --arg r "$reason" '{
                hookSpecificOutput: {
                    hookEventName: "PreToolUse",
                    permissionDecision: "allow",
                    permissionDecisionReason: $r
                }
            }'
            ;;
        *)
            : # unknown harness: say nothing
            ;;
    esac
}
