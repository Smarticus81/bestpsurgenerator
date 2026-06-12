---
name: psur-imdrf-classify
description: Classify CSI complaint Symptom Codes into IMDRF Annex A Harm and Medical Device Problem categories for PSUR Table 7. Use before building Table 7 or when complaints show as Unknown or Not yet determined. Maps every complaint to a granular leaf-node MDP and a specific Harm category.
when_to_use: Trigger when building Table 7, when complaint harm distribution shows Unknown, when IMDRF classification is needed, or when mapping symptom codes to harm and MDP categories.
allowed-tools: Bash(python3 *) Read Grep
---

# IMDRF Complaint Classification for PSUR Table 7

You classify every complaint from the CSI complaint database into a specific IMDRF Annex A **Harm** category and a specific IMDRF Annex A **Medical Device Problem** (MDP). The output feeds directly into Table 7 of the PSUR.

## Absolute Rules

1. **NEVER output "Unknown / Not yet determined" as a Harm.** Every complaint MUST be classified.
2. **NEVER output parent-level IMDRF codes as MDPs.** "Device issues, consequence or impact to patient or user unknown" is a parent node — not a valid endpoint. Classify to a leaf-node MDP.
3. **Harm = patient outcome.** Did the patient suffer an injury? What type?
4. **MDP = what the device did wrong.** Did it fail to fire? Did a component break?
5. **One complaint = one Harm + one MDP.** Every complaint appears exactly once in Table 7.

## Classification Pipeline

Run these steps in order for each complaint row:

### Step 1: Extract fields from the complaint record

Read these columns: `Symptom Code`, `Fault Code`, `Failure Code`, `Nonconformity` (narrative text), `Investigation Findings` (narrative text), `MDR Issued`, `Complaint Confirmed`.

### Step 2: Apply deterministic mapping table

Check `Symptom Code` against the mapping table below. If matched, use the mapped Harm and MDP directly — no LLM inference needed.

### Step 3: Handle `other` and unmapped codes

If `Symptom Code` = `other` or is not in the mapping table, read the `Nonconformity` narrative text and apply keyword matching (see keyword rules below).

### Step 4: Determine Harm category

- If Symptom Code = `laceration` → **Harm: Skin/Subcutaneous Injury (Laceration)**
- If narrative contains extrusion/migration/rejection → **Harm: Tissue Reaction (Staple Migration/Extrusion)**
- ALL other complaints → **Harm: No Health Consequence or Impact**

### Step 5: Validate — no Unknown allowed

If after Steps 2-4 a complaint still has no classification, assign:
- Harm: **No Health Consequence or Impact**
- MDP: **Other Device Performance Problem**

This is the fallback — it is always better than "Unknown."

## Symptom Code → IMDRF Mapping Table

This table covers known CSI Symptom Codes for the manufacturer's surgical devices. For device families not listed, apply the keyword matching in Step 3.

| CSI Symptom Code | IMDRF Harm | IMDRF Medical Device Problem |
|---|---|---|
| `laceration` | Skin/Subcutaneous Injury (Laceration) | Failure to Deliver Staple Properly / Misfire |
| `doesnotperformproperly` | No Health Consequence or Impact | Device Did Not Operate as Intended (Failure to Fire) |
| `brokenordamagedcomponent` | No Health Consequence or Impact | Component Broken or Damaged |
| `foreignmaterial` | No Health Consequence or Impact | Foreign Material in/on Device |
| `performance` | No Health Consequence or Impact | Performance Discrepancy |
| `rigidjoints` | No Health Consequence or Impact | Mechanism Stiffness / Joint Resistance |
| `shippingdamage` | No Health Consequence or Impact | Packaging/Shipping Damage |
| `incorrectquantity` | No Health Consequence or Impact | Incorrect Quantity in Package |
| `defective` | No Health Consequence or Impact | Defective Component |
| `wrongcomponent` | No Health Consequence or Impact | Wrong Component / Labeling Mismatch |
| `productsticking` | No Health Consequence or Impact | Device Did Not Operate as Intended (Mechanism Jam) |
| `leaking` | No Health Consequence or Impact | Device Leakage |
| `contaminatedproduct` | No Health Consequence or Impact | Breach of Device Sterility |
| `blurredimage` | No Health Consequence or Impact | Image Quality Degradation |
| `deviceinoperable` | No Health Consequence or Impact | Device Did Not Operate as Intended |
| `electricalissue` | No Health Consequence or Impact | Electrical Problem |
| `softwareissue` | No Health Consequence or Impact | Software Problem |
| `overheating` | No Health Consequence or Impact | Overheating |
| `lightissue` | No Health Consequence or Impact | Light Source Failure |
| `insufficientsuction` | No Health Consequence or Impact | Insufficient Fluid Flow |
| `noflow` | No Health Consequence or Impact | No Fluid Flow |

## Keyword Matching for `other` and Unmapped Codes

When `Symptom Code` = `other` or is not in the table above, read the `Nonconformity` field and match keywords:

```python
text = str(row['Nonconformity']).lower()

# Device function failures
if any(k in text for k in ['fire', 'deploy', 'stapl', 'distribut', 'dispens']):
    mdp = 'Device Did Not Operate as Intended (Failure to Fire)'
    harm = 'No Health Consequence or Impact'

elif any(k in text for k in ['jam', 'stuck', 'stick', 'lock', 'seize']):
    mdp = 'Device Did Not Operate as Intended (Mechanism Jam)'
    harm = 'No Health Consequence or Impact'

elif any(k in text for k in ['broke', 'broken', 'crack', 'snap', 'fractur']):
    mdp = 'Component Broken or Damaged'
    harm = 'No Health Consequence or Impact'

elif any(k in text for k in ['foreign', 'particle', 'debris', 'contamin']):
    mdp = 'Foreign Material in/on Device'
    harm = 'No Health Consequence or Impact'

# Patient injury indicators — these change the Harm category
elif any(k in text for k in ['cut', 'lacerat', 'nick', 'bleed', 'wound']):
    mdp = 'Failure to Deliver Staple Properly / Misfire'
    harm = 'Skin/Subcutaneous Injury (Laceration)'

elif any(k in text for k in ['extrud', 'migrat', 'reject', 'surface', 'push out']):
    mdp = 'Material Integrity / Adverse Tissue Response'
    harm = 'Tissue Reaction (Staple Migration/Extrusion)'

# Packaging and administrative
elif any(k in text for k in ['label', 'marking', 'print', 'package insert']):
    mdp = 'Labeling/Marking Issue'
    harm = 'No Health Consequence or Impact'

elif any(k in text for k in ['expir', 'outdat', 'shelf life']):
    mdp = 'Use Beyond Expiration'
    harm = 'No Health Consequence or Impact'

elif any(k in text for k in ['order', 'ship', 'wrong item', 'incorrect item']):
    mdp = 'Order/Administrative Error'
    harm = 'No Health Consequence or Impact'

elif any(k in text for k in ['sterile', 'steril', 'seal', 'pouch', 'breach']):
    mdp = 'Breach of Device Sterility'
    harm = 'No Health Consequence or Impact'

# Fallback — NEVER output Unknown
else:
    mdp = 'Other Device Performance Problem'
    harm = 'No Health Consequence or Impact'
```

## Output Format

After classifying all complaints, produce a summary for the table builder:

```
HARM/MDP CLASSIFICATION SUMMARY
================================
Total complaints classified: [N]
Unclassified (should be 0): [N]

HIERARCHY:
  Skin/Subcutaneous Injury (Laceration): [N] total
    Failure to Deliver Staple Properly / Misfire: [N]
  Tissue Reaction (Staple Migration/Extrusion): [N] total
    Material Integrity / Adverse Tissue Response: [N]
  No Health Consequence or Impact: [N] total
    Device Did Not Operate as Intended (Failure to Fire): [N]
    Component Broken or Damaged: [N]
    Foreign Material in/on Device: [N]
    [... every MDP with ≥1 complaint]
  Grand Total: [N]

VERIFICATION:
  Sum of all MDPs = [N] (must equal total complaints)
  Sum of Harm subtotals = [N] (must equal Grand Total)
```
