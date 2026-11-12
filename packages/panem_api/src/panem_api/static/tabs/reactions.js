// Reaction-event pop-ups and your archetype score -- Discord-only for
// now (`/reaction archetype|respond`, `panem_bot.cogs.reactions`). Not
// built out as a dashboard tab yet.
import { mountPlaceholder } from "./_placeholder.js?v=7";

export function mount(root) {
  return mountPlaceholder(root, "Reactions", "/reaction archetype or /reaction respond");
}
