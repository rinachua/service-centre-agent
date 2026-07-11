# Etch Chamber RF Over-Reflection Troubleshooting Guide

Applies to: RF-driven etch chambers (e.g. ETCH-07 class tools).

Symptom: RF-OVR-REFL alarm during an etch step, process aborts.

## Step 1: Check RF match network
Over-reflection is most commonly caused by a degraded RF match network. Inspect
the match network cable connections for looseness or corrosion. Reseating the
cable resolves a majority of first-time occurrences.

## Step 2: Inspect match network components
If the alarm recurs after reseating the cable, inspect the match network
capacitor and inductor for wear. A failing capacitor is a common root cause of
repeat RF-OVR-REFL alarms within a short window (days, not months).

## Step 3: Escalate if recurrence continues
If RF-OVR-REFL recurs a third time after a component replacement, escalate to
engineering for a full RF generator preventive maintenance check, including
generator output calibration. Continuing to run the recipe without escalation
risks further chamber downtime and possible generator damage.
