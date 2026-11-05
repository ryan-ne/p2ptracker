# UX Review — MMK P2P Tracker

**What's reviewed:** the live product as a whole: the web dashboard (public view and signed-in view, desktop and phone), the sign-in page, and the Telegram group experience (price alerts, pattern alerts, bot commands).
**Who uses it:**
- **The trader** (signed in): checks where USDT is cheapest, sets price alerts, acts when an alert arrives.
- **Group members and visitors** (not signed in): glance at the rate on the dashboard and read alerts in the Telegram group.
- **The admin** (rare): adds users over SSH. Out of scope here.

---

## 1. What Works Well

**The answer is above the fold.** Open the dashboard and the first thing under the header is "Cheapest buy 4,865 · Bybit". The question people come with is "where is USDT cheapest right now?", and the page answers it in zero taps, before any scrolling, on desktop and phone. Most monitoring tools bury this under charts. This one doesn't.

**Alerts meet people where they already are.** Nobody has to keep a browser tab open. Price alerts and pattern alerts land in the Telegram group the team already reads. Pattern alerts come with a plain-language summary in Burmese, so the "what just happened?" explanation arrives with the alert. This is the strongest part of the product.

**Alerts are quiet by design.** A price alert fires once when the price *crosses* the line, then waits. A pattern alert won't repeat for 30 minutes and ignores false moves after a restart. Users don't learn to mute a channel that only speaks when something changed.

**Exchange status is honest.** Each exchange card says "● live", "● no ads" or "● error" with the reason. When Bybit misbehaves, people see it instead of quietly trusting a stale number.

**The public view is the right default.** Group members can open a link and see the rate with no account. Only changes need a sign-in, and the server enforces it. Nobody is turned away at a sign-in wall.

**The chart now tells one story at a time.** One exchange, green buy, red sell, the gap shaded, and "Buy now / Sell now / Spread now" above it. It reads in two seconds.

**Phones are first-class.** The bottom tab bar, the price strip that fits the screen and the two-line alert rows make the phone experience feel designed, not shrunk.

---

## 2. The Core Tension

**People come for one decision: *where should I buy USDT, and is now a good moment?* But the dashboard is laid out like a trading terminal, and it stops one step short of the action.**

Everything is symmetrical: buy and sell side by side, best and avg 5, spread, exchange gap, every exchange equal. The real interest is lopsided: you already told the pattern agent to watch **buy only**, but the rest of the product still treats sell as equally important. And when the dashboard *has* answered the question ("Bybit, 4,865, merchant QuickSwap_Pro"), there's nowhere to go. The user leaves the app, opens the exchange, navigates to P2P, picks USDT and MMK, and hunts for the ad they were just looking at.

---

## 3. The User's Day

### The trader: "Where do I buy right now?"

**Today:**
1. Open the dashboard and read "Cheapest buy 4,865 · Bybit" (0 taps).
2. Scroll to the ad list to see *who* is selling and whether their limits fit the amount (1 scroll).
3. Pick an ad mentally. The row can't be tapped.
4. Open the Bybit app, go to P2P, choose USDT, set MMK, make sure "Buy" is selected, scroll to find that merchant (5–7 taps in another app).

**Friction count:** 0 taps to the answer, then **5–7 taps in another app** to act on it.

**What it should feel like:** the trader reads "Cheapest buy 4,865 · Bybit", taps it, and lands on Bybit's USDT/MMK buy page, or on that merchant's ad when the exchange supports a direct link. Every ad row works the same way. The dashboard becomes the start of the purchase, not a detour before it.

**The gap:** the last mile. The app knows the exchange, the side, the currency and often the ad itself, and throws that away at the moment it matters most.

### The trader: "Tell me when buying gets cheap"

**Today (desktop):**
1. Scroll past the exchange cards, the 10-row ad list and the chart to reach "Price alerts" (3–4 long scrolls).
2. Fill in six fields:
   - **Exchange** (defaults to "Any", fine)
   - **Side** (Buy, fine)
   - **Metric** ("Best" or "Avg 5": what's Avg 5?)
   - **When** ("≤ at or below")
   - **Price**: the current price isn't shown here, so you scroll back up to remember it
   - **Cooldown (s)**: 300 seconds means what, exactly?
3. Press Add alert.

On a phone, the Alerts tab gets you there in one tap, but the six fields are the same.

**Through Telegram today:** remember `/add bybit buy best <= 4850`, six words in a fixed order with a symbol in the middle. Most people will get it wrong the first time, and the error message names internal terms ("metric must be best or avg5").

**Friction count:** 3–4 scrolls, 6 decisions (2 of them jargon), 1 trip back up to check the current price.

**What it should feel like:** next to "Cheapest buy 4,865" sits a small bell. Tap it and you're asked one question: *"Notify me when buying drops to …"*, with the price pre-filled a little below the current one and the current price shown beside the box. One tap to confirm. Exchange defaults to "any", side to buy (the direction you care about), and "best" price. Avg 5 and the cooldown become optional "More options", and the cooldown is in minutes.

**The gap:** the alert form asks the trader to configure a rule engine. The trader wants to say a number.

### The trader: an alert arrives

**Today, in the group:**
> 🔔 **Bybit BUY** best **4,820 MMK** ≤ 4,850
> best 4,820 · avg5 4,831 · 12 ads
> rule #3 · 2026-11-05 14:02

The useful part (Bybit, 4,820) is there, but it's wrapped in system vocabulary: "best", "avg5", "rule #3". Group members who didn't create the rule can't tell what "rule #3" means. To act, the reader leaves Telegram and repeats the 5–7 tap journey above. If they want context, there's no link to the dashboard.

**What it should feel like:**
> 🔔 **Buy USDT on Bybit: 4,820 MMK** (your alert: below 4,850)
> Cheapest right now. [Open on Bybit] · [See dashboard]

One line that reads like a sentence, the target for context, and a link to act.

**The gap:** the alert is accurate but written for the person who built the rule engine, not the people reading the group.

### Group members and visitors: "What's the rate today?"

**Today:** they open the link, see the price strip and the exchange cards, and understand "Cheapest buy". Then they meet "Exchange gap −31", "Spread (buy − sell) 30", "avg 5 · 20 ads" and an "Update every 10s" dropdown they can't change. Buy and Sell are only explained in the chart legend, halfway down the page.

**What it should feel like:** a visitor sees the buy price prominently, with the exchange and a short line like "what you pay for 1 USDT". Sell is visible but secondary. Controls they can't use simply aren't there.

---

## 4. What to Cut

| What the user sees | Why it should go | What replaces it |
|---|---|---|
| **"Update every" in the top bar** | It's a setting changed maybe once a month, given the most valuable spot on every screen. Visitors see it locked, which is noise. | Move it into a "Settings" area beside Ad filters, signed-in only. The "Live · 14:02" indicator stays. |
| **The "Pause alerts" switch in the top bar** | A single tap silences every alert for the whole group, with no confirmation and no reminder it's on. It's too easy to hit by accident and too easy to forget (see "What's Missing"). | Move it to Settings, with a confirmation, and show a banner while paused. |
| **"Exchange gap" in the price strip** | "Best sell − cheapest buy", usually negative. Only an arbitrage trader cares, and it takes a third of the strip on phones. | Drop it from the strip; it can live on the exchange cards or the chart if wanted. |
| **"Avg 5" everywhere by default** (cards, chart toggle, alert form, Telegram) | It's an internal measure (average of the top 5 ads). Useful, but it doubles the numbers people read without telling them which to trust. | Keep "best" as the headline. Mention avg 5 once, explained, on the cards ("typical of the top 5 ads"). Move it to "More options" in the alert form, and remove it from Telegram alerts. |
| **The "Cooldown (s)" field** | Seconds are the wrong unit, and almost nobody should change it. | Default to 5 minutes, under "More options", in minutes. |
| **"rule #3" in Telegram alerts** | Means nothing to anyone except the person who set it up. | State the target in words: "your alert: below 4,850". |

---

## 5. What's Missing

**1. "Open on the exchange" from the price and from every ad.** *(Transforms the daily workflow.)*
Tapping "Cheapest buy · Bybit", an exchange card, or an ad row should open that exchange's USDT/MMK P2P buy page, or the specific ad where possible. Price alerts in Telegram should carry the same link. This turns the tracker from "tells me where" into "takes me there", and removes 5–7 taps from every purchase.

**2. A one-question alert, started from the price.** *(Transforms the alert workflow.)*
A bell beside the cheapest buy price and on each exchange card: "Notify me when buying drops to [4,840]". Pre-filled, current price shown, one tap to save. The full form stays for unusual cases. Each saved alert should also show how far it is from today's price ("4,840, 25 below now"), so the trader can see which alerts are close to firing.

**3. A visible "Alerts paused" state.** *(Prevents silent failure.)*
When alerts are paused, a yellow banner across the dashboard should say "Alerts are paused, nobody in the group will be notified" with a Resume button. Telegram should get one reminder if it stays paused for hours. Today the only signal is a small switch in the corner, and a forgotten pause means missed buying opportunities with no clue why.

**4. Telegram alerts written for readers.** *(Moderate impact, daily.)*
Lead with the action and price ("Buy USDT on Bybit: 4,820 MMK"), show the target in words, and add links to the exchange and the dashboard. Price alerts could use the same Burmese wording as pattern alerts, so the whole group reads the same language.

**5. Buy-first emphasis.** *(Moderate impact, every visit.)*
You've said buy is what matters. The dashboard should reflect that:
- buy shown larger on the cards
- the chart opening on "Buy" rather than "Both"
- a short "Buy = what you pay for 1 USDT" near the top, not only in the chart legend

Sell stays one tap away.

**6. "Filters are on" visibility.** *(Prevents confusion.)*
Ad filters change the prices *everyone* sees, and the alerts, but once saved, nothing on the page says so. When a filter is active, the ad list, the cards and the price strip should say "Filtered: KBZPay only · orders from 500,000 MMK", and the filter screen should warn "This changes prices and alerts for everyone". Otherwise someone will one day wonder why the dashboard disagrees with the Bybit app.

**7. Names for Bybit's unnamed payment methods.** *(Polish.)*
The filter list shows "#604" and "#77" next to "KBZPay" and "WavePay". People can't choose what they can't recognise. Name them, or hide unknown ones under "Other".

---

## 6. Priorities

Ordered by how much friction they remove, for how many people, how often.

**Structural changes (change how people use the app):**
1. **Open on the exchange** from the price strip, the cards, the ad rows and Telegram alerts. Every person, every time they act on a price: the biggest saving in the product.
2. **One-question alerts** from the price, with the distance-to-price shown on saved alerts. Turns alert setup from a form into a sentence.
3. **"Alerts paused" banner and reminder.** Rare, but the failure is invisible and costly (missed buys for the whole group), so it ranks high.
4. **Telegram alerts rewritten for readers** (action first, target in words, links). Read by the whole group, many times a day.

**Surface refinements (make what exists clearer):**
5. **Buy-first emphasis and less jargon:** demote avg 5 and the exchange gap, and explain buy/sell near the top.
6. **Move rare settings out of the top bar** (update interval, pause) into a signed-in Settings area.
7. **Show when filters are on**, and warn that filters affect everyone.
8. **Name or hide Bybit's unknown payment IDs.**

Do 1–4 before 5–8. The second group makes the screens tidier; the first changes what someone actually does between seeing a price and buying USDT, which is the reason the app exists.
