// Personal mystery chains and global-event standings -- Discord-only for
// now (`/event mystery|deliver|status|info|leaderboard`,
// `panem_bot.cogs.events`). Not built out as a dashboard tab yet.
import { mountPlaceholder } from "./_placeholder.js?v=7";

export function mount(root) {
  return mountPlaceholder(root, "Events", "/event mystery, /event deliver, or /event leaderboard");
}
