# Decide the unit of menu engineering analysis

Type: grilling
Status: open

## Question

What is the *thing* that gets classified as a Star, Plowhorse, Puzzle or Dog —
and what is the set it is measured against?

This is the keystone of the whole analysis track. Nearly every downstream
question (how the product↔recipe map is shaped, what the classification
thresholds mean, what the app displays) takes its shape from this answer.

Three candidate units are all defensible and all already present in the repo:

- A **Toast item** — what a guest actually orders, and what carries revenue.
- A **Product** (`CONTEXT.md`) — the forecasting unit, deliberately abstracted
  across Toast's multiple spellings and across the item/modifier split. Note
  bagels are sold *only* as modifiers, with no menu item per flavor
  (`normalize.py:18`) — so a Product can have no Toast item at all.
- A **MarginEdge recipe** — what carries cost, and what `product_map.csv`
  already matches against.

The three do not agree, and the disagreement is not cosmetic. Kasavana & Smith's
popularity threshold is `(1 ÷ number of menu items) × 0.70`, so **the choice of
unit changes the denominator and therefore changes which items classify as
popular** — the same item can be a Star under one unit and a Dog under another.

Also settle:

- **What is in the set at all.** Retail cans of soda, day-old discounted bagels,
  catering, and modifiers-sold-as-products all distort the mix differently.
  Is the analysis over one menu, one category at a time, or everything?
- **Whether the existing Product concept stretches to cover this**, or menu
  engineering needs its own unit alongside it. If a new term is needed, coin it
  in `CONTEXT.md` with `/ubiquitous-language`.

Mike has a from-scratch stats background: build up what the sales-mix share and
the 70% threshold actually *do* before asking for a choice. Leave an ADR.
