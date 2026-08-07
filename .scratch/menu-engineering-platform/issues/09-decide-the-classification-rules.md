# Decide the classification rules and thresholds

Type: grilling
Status: open
Blocked by: 08

## Question

Given the unit ticket 08 fixed — what exactly makes something a Star, Plowhorse,
Puzzle or Dog *here*?

The textbook rules (`menu_engineering/modifiers-in-menu-engineering.md`) are:
contribution margin = selling price − portion cost; an item is "popular" if its
sales-mix share is at least 70% of an equal share; margin is high or low against
the menu's average contribution margin. Those are defaults, not laws, and this
business breaks several of their assumptions.

Decide:

1. **The period.** Over what window is the mix computed — trailing 4 weeks, a
   quarter, per-season? A deli's mix moves with weather and holidays. Too short
   is noise; too long hides a real decline.

2. **Whether classification is a point-in-time verdict or a trend.** An item
   drifting from Star to Plowhorse is more actionable than its current quadrant.
   This changes what gets stored, not just what gets displayed.

3. **The margin axis with Realized Cost.** `CONTEXT.md` defines Realized Cost as
   base recipe cost plus the signed cost of every attached Modifier. Confirm the
   margin axis uses Realized Cost, not Base Recipe cost — and decide what
   happens to an item whose recipe is unmapped or whose cost is stale. Excluding
   it changes the denominator; including it at a wrong cost poisons the average.

4. **The 70% threshold and the margin cutoff.** Keep the textbook values or set
   them from this menu's actual distribution? A menu with a few dominant items
   and a long tail behaves badly under an equal-share assumption.

5. **Statistical honesty.** A rarely-sold item's margin and share are both
   estimated from few observations. Decide whether there is a minimum volume
   below which an item is unclassified rather than confidently called a Dog.

Mike's stats background is from scratch: derive the sales-mix share, the
threshold, and why the quadrant boundaries sit where they do from first
principles before asking for choices. Where variants are defensible with no
evidence between them, offer deferral. Leave an ADR.
