# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                               Scripts - ChatGPT Setup                                                #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Connect Aurum to your ChatGPT plan and remember an available model.

This command signs you in when necessary, reads the available model list and saves your selection. It verifies
authentication and model availability without sending an inference request. It does not load account
configuration, financial statements, categorisation instructions or the database.

Run from the project root: python aurum/scripts/chatgpt_setup.py
Use --model MODEL to choose an available model, or --sign-in to authorise Aurum again.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
import argparse
from pathlib import Path
import shlex
import sys

# Project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Direct execution starts import searches in scripts/. Make the project root available for Aurum imports.
if __package__ in (None, ""):
    sys.path.insert(0, str(PROJECT_ROOT))

# Aurum imports
from aurum.ai.chatgpt import ChatGPTConnection, ChatGPTError, connect


# %% 1. Setup ------------------------------------------------------------------------------------------------
def main() -> int:
    """
    Verify ChatGPT access, select an available model and save the choice without running inference.

    Returns:
        Zero when access and model selection succeed, one for a reported failure, or 130 if cancelled.
    """
    parser = argparse.ArgumentParser(description="Sign in to ChatGPT and select Aurum's model.")
    parser.add_argument(
        "--model", help="Available model slug; otherwise reuse the saved choice or show a menu."
    )
    parser.add_argument(
        "--sign-in", action="store_true", help="Open a fresh ChatGPT sign-in instead of reusing it."
    )
    args = parser.parse_args()

    print("Aurum | ChatGPT setup")
    print("Checking sign-in and available models...")
    try:
        with connect(force_sign_in=args.sign_in) as connection:
            model = _choose_model(connection, args.model)

    except ChatGPTError as error:
        print(f"Setup failed: {error}", file=sys.stderr)
        print("Retry setup: python aurum/scripts/chatgpt_setup.py", file=sys.stderr)
        return 1
    except ValueError:
        # Keep private connection details out of unexpected validation messages.
        print("Setup failed: connection details or the model list did not pass validation.", file=sys.stderr)
        print("Retry setup: python aurum/scripts/chatgpt_setup.py", file=sys.stderr)
        return 1
    except OSError:
        print("Setup failed: a local file or connection could not be accessed.", file=sys.stderr)
        print("Retry setup: python aurum/scripts/chatgpt_setup.py", file=sys.stderr)
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nSetup cancelled.", file=sys.stderr)
        return 130

    print(f"ChatGPT ready. Saved model: {model}.")
    print("Next: " + shlex.join(["python", "aurum/scripts/journal.py", "--model", model]))
    return 0


# %% 2. Helpers ----------------------------------------------------------------------------------------------
def _choose_model(connection: ChatGPTConnection, requested: str | None) -> str:
    """
    Reuse a saved available model or ask for an explicit selection, then remember the choice.

    Args:
        connection: An authenticated ChatGPT connection.
        requested: An optional model slug supplied on the command line.

    Returns:
        The selected model slug.

    Raises:
        ChatGPTError: If there are no available models or the selection is invalid.
    """
    models = connection.list_models()
    if not models:
        raise ChatGPTError("Your ChatGPT account did not return any available models.")

    available = {model["slug"] for model in models}
    saved = connection.model
    if requested is not None:
        if requested not in available:
            raise ChatGPTError(
                "The requested model is unavailable. Run without --model to choose from the list."
            )
        selected = requested
    elif saved is not None and saved in available:
        selected = saved
    else:
        print("\nChoose an available model for Aurum:")
        for number, model in enumerate(models, start=1):
            print(f"  {number}. {model['display_name']} ({model['slug']})")
        answer = input("Model number: ").strip()
        choices = {str(number): model["slug"] for number, model in enumerate(models, start=1)}
        if answer not in choices:
            raise ChatGPTError("Enter one of the model numbers shown; no model selection was saved.")
        selected = choices[answer]

    connection.select_model(selected)
    return selected


# %% 3. Execution --------------------------------------------------------------------------------------------
if __name__ == "__main__":
    sys.exit(main())
