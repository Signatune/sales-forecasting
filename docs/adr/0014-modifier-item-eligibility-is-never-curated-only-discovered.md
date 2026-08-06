# Modifier eligibility per item is discovered from order data, never curated

The Toast-to-MarginEdge menu-engineering mapping tool maps each Modifier to a
recipe once, independent of which Products it rides on, rather than also
letting a reviewer declare which Modifiers are eligible on which Product. That
eligibility is left to fall out of the order data itself when it's ingested —
if "sesame bagel" shows up nested under a "Baker's Dozen" selection in a raw
order, that's what makes it eligible there, not a maintained list in this
tool.

We considered curating eligibility explicitly (closer to how a menu-config
tool would model it) but rejected it: this tool exists for a one-time mapping
pass, used occasionally thereafter, not for ongoing menu administration. Toast
is the actual source of truth for which Modifiers attach to which Products;
maintaining a second, separate record of that same relationship here would
drift from Toast's own configuration the moment either one changed, with
nothing to catch the divergence.
