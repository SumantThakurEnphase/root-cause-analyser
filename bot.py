"""
Microsoft Teams Bot — handles incoming messages and delegates to the RCA Agent.
"""

import json
import traceback

from botbuilder.core import ActivityHandler, TurnContext, MessageFactory, CardFactory
from botbuilder.schema import Activity, ActivityTypes

from agents.rca_agent import RCAAgent


class RCABot(ActivityHandler):
    def __init__(self):
        self.rca_agent = RCAAgent()

    async def on_message_activity(self, turn_context: TurnContext) -> None:
        """Handle incoming messages from Teams users."""
        query = turn_context.activity.text or ""
        query = query.strip()

        if not query:
            await turn_context.send_activity(
                MessageFactory.text("Please provide an error description to analyze.")
            )
            return

        # Handle special commands
        if query.lower() in ("/help", "help"):
            await self._send_help(turn_context)
            return

        # Send a "thinking" indicator
        await turn_context.send_activity(
            Activity(type=ActivityTypes.typing)
        )
        await turn_context.send_activity(
            MessageFactory.text("🔍 Analyzing your error report... This may take a moment.")
        )

        try:
            # Run the RCA pipeline
            rca_result = await self.rca_agent.analyze(query)

            # Send the result as an Adaptive Card
            card = self._build_adaptive_card(query, rca_result)
            message = MessageFactory.attachment(
                CardFactory.adaptive_card(card)
            )
            await turn_context.send_activity(message)

        except Exception as e:
            error_msg = f"❌ Error during analysis: {str(e)}"
            print(f"RCA Bot Error: {traceback.format_exc()}")
            await turn_context.send_activity(MessageFactory.text(error_msg))

    async def on_members_added_activity(self, members_added, turn_context: TurnContext):
        """Welcome new members when they join the conversation."""
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                welcome = (
                    "👋 **Welcome to the Root Cause Analyser Bot!**\n\n"
                    "I can help you identify the root cause of errors in the Solargraf platform.\n\n"
                    "**How to use:**\n"
                    "Simply describe the error you're investigating, for example:\n"
                    "- _\"Download DWG from SDT/EDT is failing\"_\n"
                    "- _\"Auto-design is timing out during panel placement\"_\n"
                    "- _\"3D rendering crashes with null geometry\"_\n\n"
                    "Type `/help` for more info."
                )
                await turn_context.send_activity(MessageFactory.text(welcome))

    async def _send_help(self, turn_context: TurnContext) -> None:
        help_text = (
            "🤖 **Root Cause Analyser Bot — Help**\n\n"
            "**What I do:**\n"
            "I analyze error reports by searching SigNoz logs and the Solargraf codebase "
            "(solargraf-api, graf-apps, design-tool) to identify the root cause.\n\n"
            "**How to use:**\n"
            "Just send me a message describing the error. Be as specific as possible.\n\n"
            "**Example queries:**\n"
            "- `Download DWG from SDT/EDT is failing`\n"
            "- `Auto-design timeout during panel placement`\n"
            "- `3D rendering crash on proposal view`\n"
            "- `Financial calculation returning NaN`\n"
            "- `Screenshot generation failing`\n\n"
            "**What I return:**\n"
            "A structured Root Cause Analysis including:\n"
            "- Root cause explanation\n"
            "- Affected file(s) and function(s)\n"
            "- Evidence from logs\n"
            "- Suggested fix\n"
            "- Confidence level"
        )
        await turn_context.send_activity(MessageFactory.text(help_text))

    def _build_adaptive_card(self, query: str, rca_result: str) -> dict:
        """Build a Teams Adaptive Card with the RCA result."""
        return {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": [
                {
                    "type": "TextBlock",
                    "text": "🔍 Root Cause Analysis",
                    "weight": "Bolder",
                    "size": "Large",
                    "color": "Accent",
                },
                {
                    "type": "TextBlock",
                    "text": f"**Query:** {query}",
                    "wrap": True,
                    "spacing": "Small",
                },
                {
                    "type": "TextBlock",
                    "text": "---",
                    "spacing": "Small",
                },
                {
                    "type": "TextBlock",
                    "text": rca_result,
                    "wrap": True,
                    "spacing": "Medium",
                    "fontType": "Default",
                },
            ],
            "padding": "Default",
        }
