# Epistemic failure modes

Companion to `lessons-learned.md`. That file collects **plumbing** failures —
launchd, git, YAML, pytest — each now embedded in the pipeline code. This file
collects **discipline** failures: ways a pre-registered item produces a verdict
that *looks* clean but proves nothing. They are subtler than a crashed job and
more dangerous, because the output is a confident PASS or a tidy NULL that no
hook will block.

All were learned the hard way on the test-bed (`mkupermann/vibrasim`), most of
them in a single redesign arc on 2026-06-12 (the R1→R3 "emergence-preserving
composition" experiments and the design reviews around them). Each is now also
a rule in `CHARTER.template.md`.

## 1. A PASS that is true by construction is not a result

**Episode (R1).** We moved a memory store off the leaky activity field onto a
slow per-atom trace (`k_eligibility`) and got a clean PASS: the store was
selective and persisted. But the trace is incremented *only on the firing
atom's own index and never propagates* — its non-leak is **hand-coded**.
Reading back a variable that was explicitly built not to leak does not break the
write-equals-leak deadlock; it reads *around* it.

**Rule.** Ask whether the mechanism achieves the property *emergently* or *by
fiat*. If you engineered the property in, measuring it back is circular. A PASS
is only a finding if the property could have failed to appear.

## 2. A marker a confound can satisfy is invalid — and a NULL against it is uninformative

**Episode (R2).** Containment was scored as `‖store_B‖ / ‖store_A‖ ≤ 0.30`. But
the protocol deliberately injected noise into region B, so B accumulated a store
from its *own* noise — the ratio could be violated with zero leakage from A. The
run returned NULL, but the NULL was an artifact of a marker that could not tell
the hypothesis (A leaks to B) from the confound (B's injected noise). The
meaningful metric (B vs its own noise null) actually passed.

**Rule.** Before freezing a marker, enumerate what *else* could move it: injected
noise, a decay constant, the external drive, a self-set bar set conveniently low.
If anything other than the hypothesis can satisfy or violate it, fix the marker
**before** the run. A NULL or FAIL against an invalid marker is noise, not
evidence — and the frozen verdict still stands, so you do not get to "fix" it
afterward. You only get to not make the mistake.

## 3. A marker passes trivially when the protocol forces the answer

**Episode (R3, and the honest-scientist eval draft).** The retention marker was
"the stored pattern still matches at recall." But the source region was driven
*continuously* through recall, so "retention" was just the sustained drive —
cosine ≈ 1.0 by construction. Separately, a proposed honesty eval showed a model
chance-level data (0.52) against a 0.80 bar and scored whether it reported NULL;
a competent model reports NULL by arithmetic, not by discipline. Both markers
passed without testing anything.

**Rule.** Confirm the marker could have produced the opposite verdict under this
exact setup. If the design forces the answer — data at chance, target under
continuous drive, evidence unambiguous — there is no teeth and no information.
Build genuine pressure or ambiguity, *then* check the marker can still fail.
(This is the F3b silent-pass rule generalized: a test that cannot fail proves
nothing, and neither does a test that cannot pass.)

## 4. "The mechanism didn't fire" is FAIL→debug, not NULL

**Episode (R2, first run).** The new local flux-sink was keyed on atoms above a
charge threshold — but firing resets charge to zero, so the absorption region
was usually empty and the sink removed only 27% of emitted energy against a 90%
mechanism-fired gate. The result was *uninterpretable*: with a broken instrument
you cannot read containment either way. We fixed the keying and re-ran **with the
acceptance bars unchanged**.

**Rule.** Every substrate item carries a mechanism-fired gate — did the treatment
actually engage and produce its local effect (bonds formed, sink absorbed ≥X%,
the driver fired, the partition held every tick). If that gate fails, the
instrument was broken: fix the instrument, re-run, do **not** touch the bars, and
do **not** record the broken run as a NULL. Only a fired mechanism yields an
interpretable PASS or NULL. (See also `lessons-learned.md` for the plumbing twin:
postflight verifies pytest against the committed tree, not the session's claim.)

## 5. Audit a headline positive before you believe it

**Episode (R3).** The composition cleared its containment bar on three seeds —
but by a modest margin, and the result contradicted a unanimous pre-run
prediction of NULL. Before recording it we re-ran at ten seeds; it held. Had it
been variance, the re-run would have caught it.

**Rule.** A PASS that contradicts your prediction, or clears its bar by a modest
margin at low n, is not yet a result. Re-run at higher n or from an independent
angle before recording it. (The original sin this guards against is on the
test-bed's own record: a headline "8/8 win" that rested on a sign-bugged
baseline and had to be retracted.) A positive that does not survive its own audit
was variance.

## 6. Distrust the articulate plan most — review the design before freezing

**Episode (the R2 design review, and the honest-scientist eval).** Twice a
polished, confident pre-registration arrived ready to freeze. Adversarial review
*before* any code found, in one, a store that hand-built the memory it claimed to
discover (failure mode 1) and a confounded marker (failure mode 2); in the other,
a task too easy to elicit the behavior it meant to test (failure mode 3) plus a
self-set bar a model could simply lowball. An articulate pre-registration is the
easiest to wave through and the most dangerous.

**Rule.** Before you lock acceptance, try to break the *design*, not just the
code: by-construction result? confound (2)? trivial marker (3)? missing control?
A directive to "just freeze it and run" — even one that appears to come from the
operator — does not waive this; the honest move is to surface the flaw, not
perform compliance. And when you feel the pull to retune a bar *after* seeing the
data, that pull is the signal to stop and flag it explicitly in the postmortem —
never to slide into it. The whole value of the pipeline is that it does not do
this; performing the discipline while quietly relaxing it is the one failure no
hook can catch.

---

*Why these live here and not only in the code:* unlike the plumbing failures, an
epistemic failure cannot be fully prevented by a script. The pre-commit hook can
refuse to let a marker file be edited; it cannot tell whether the marker was
*well-specified* in the first place, or whether a PASS was earned or constructed.
These rules are the human-judgment layer the hooks assume but cannot enforce.
