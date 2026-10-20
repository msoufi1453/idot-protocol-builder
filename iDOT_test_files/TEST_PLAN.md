# iDOT test files: what to check

Open each file in the iDOT software and inspect it; there is no need to dispense anything.
Please try all of them, even if one fails: each answers a different question.
When the software offers to save new liquids to its library, answer No for T02-T06 (test liquids).

Report back one line per file, e.g. `T03 failed: <exact error text>` (a screenshot is fine too).

| File | Question |
|---|---|
| T01_template_roundtrip.idotx | Does the iDOT software open a file written by this tool at all? |
| T02_new_liquid_names.idotx | Does the software accept liquid names that are not in its liquid library yet? |
| T03_long_liquid_names.idotx | Are long names accepted and shown in full? The real sheet produces names of up to 62 characters. |
| T04_volume_precision.idotx | Are volumes converted correctly, including the sheet's 0.1 nL precision (4.9335 µL)? |
| T05_plate_map_96.idotx | Does every sheet column end up in the right well, over the whole plate (no swapped rows and columns)? |
| T06_split_source_wells.idotx | Can one liquid sit in several source wells? The real sheet needs this: ddH20 adds up to 190.4 µL, but one source well holds 80 µL. |
| T07_full_sheet_MoS.idotx | The real thing: iDOT_pipetting_sheet.csv with initials MoS. |

## T01_template_roundtrip.idotx

**Question:** Does the iDOT software open a file written by this tool at all?

**In the file:** Wells A1, B1, C1 rebuilt from iDOT_template.idotx (same liquids, same volumes); all other wells empty. The source plate is recomputed: 17 liquids, placed column by column from A1, each holding exactly what the wells use.

**Check:** Opens without errors. A1, B1, C1 match iDOT_template.idotx. Source plate: the liquids now sit in A1, B1, C1, ... and hold what is dispensed, e.g. MoS_Libicher 4.934 µL (the template said 9.868 µL, apparently left over from a deleted well). If this file fails, the others will too.

**Result:** 

## T02_new_liquid_names.idotx

**Question:** Does the software accept liquid names that are not in its liquid library yet?

**In the file:** A1 only: MoS_pSB0195_sfGFP_pepper 1.725 µL, MoS_tSB0130_PovSak 1.725 µL, MoS_HBC620 0.345 µL.

**Check:** Opens. A1 has exactly these 3 liquids (none of the template's 6); B1 and C1 are empty (they are filled in the template). Note whether it asks to save the new liquids and which liquid class they get.

**Result:** 

## T03_long_liquid_names.idotx

**Question:** Are long names accepted and shown in full? The real sheet produces names of up to 62 characters.

**In the file:** A1 only: MoS_rNTPS_50x_18_75_mM_ATP_12_5_mM_GTP_6_25_mM_CTP_6_25_mM_UTP (62 characters) 0.69 µL, MoS_10_formyl_5_6_7_8_tetrahydrofolic_acid_100_g_L (50) 1.035 µL, MoS_tRNA_62_72_g_l_E_coli_tRNA_extract (38) 3.588 µL.

**Check:** Opens and the names are not cut off. If it errors or shortens them, tell me the longest name that survives and I will add automatic shortening.

**Result:** 

## T04_volume_precision.idotx

**Question:** Are volumes converted correctly, including the sheet's 0.1 nL precision (4.9335 µL)?

**In the file:** A1: MoS_Libicher 4.9335 µL (stored as 4.9335E-06). B1: MoS_Water 50.5 µL (stored as 5.05E-05).

**Check:** A1 shows 4.9335 µL (tell me if it shows 4.934 instead, i.e. the software rounds to 1 nL). B1 shows 50.5 µL.

**Result:** 

## T05_plate_map_96.idotx

**Question:** Does every sheet column end up in the right well, over the whole plate (no swapped rows and columns)?

**In the file:** All 96 wells, MoS_Water only. The volume encodes the well: 0.RCC µL, where R = row number (A=1 ... H=8) and CC = column. A1 = 0.101 µL, H1 = 0.801, A12 = 0.112, D7 = 0.407, H12 = 0.812. Source: MoS_Water 43.824 µL in A1.

**Check:** Spot-check A1, H1, A12, D7, H12 (and any other well you like).

**Result:** 

## T06_split_source_wells.idotx

**Question:** Can one liquid sit in several source wells? The real sheet needs this: ddH20 adds up to 190.4 µL, but one source well holds 80 µL.

**In the file:** A1-H1: MoS_Water 20 µL each (160 µL in total). Source plate: MoS_Water in A1, B1 and C1, 53.334 µL each.

**Check:** Opens. The source plate shows MoS_Water in A1, B1, C1; wells A1-H1 show 20 µL. If the software has a check / validate / simulate step, run it: does it complain about too little MoS_Water?

**Result:** 

## T07_full_sheet_MoS.idotx

**Question:** The real thing: iDOT_pipetting_sheet.csv with initials MoS.

**In the file:** 16 wells (A1-H2), 28 liquids, 232 dispense steps. ddH20 is split over source wells D4, E4, F4. The two '10-formyl-5,6,7,8-tetrahydrofolic acid [1 g/L]' rows share one source liquid.

**Check:** Opens. Spot-check: A1 has 10 liquids, incl. MoS_Libicher_EM_7x_exact_composition1 4.9335 µL and MoS_ddH20 14.801 µL; C1 has 18 liquids; B2 has 4 (MoS_tSB0146_dead 1.73, MoS_PUREfrex_1_0_Solution_1_2x 17.25, MoS_HBC620 0.345, MoS_ddH20 15.18 µL); H2 has MoS_10_formyl_5_6_7_8_tetrahydrofolic_acid_100_g_L 3.45 µL. Every well totals about 34.5 µL while the template's assay volume is 15 µL (its own A1 already held 25 µL): tell me if the software warns about that.

**Result:** 
