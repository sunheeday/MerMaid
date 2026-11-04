# An RTOS-Based Agentic System for Proactive Car Sickness Intervention

**Group: MerMaid** · ITE351 AI & Applications, Hanyang University (2026 Fall)

| Name | Department | University | Email |
|---|---|---|---|
| Seunghee Kim | Dept. of Information Systems | Hanyang University | ksh0401@hanyang.ac.kr |
| Yookyung Jung | Dept. of Information Systems | Hanyang University | diesel2580@hanyang.ac.kr |
| Junmin Lee | Dept. of Information Systems | Hanyang University | junemin03@hanyang.ac.kr |
| Hyeokjin Lee | Dept. of Information Systems | Hanyang University | dlgurwls0529@hanyang.ac.kr |

**🎬 Video presentation:** _TODO: add YouTube link_ <!-- e.g. [![Video](https://img.youtube.com/vi/VIDEO_ID/0.jpg)](https://www.youtube.com/watch?v=VIDEO_ID) -->

---

## Table of Contents

- [Abstract](#abstract)
- [I. Introduction](#i-introduction)
  - [A. Motivation](#a-motivation)
  - [B. Problem Statement](#b-problem-statement)
  - [C. What We Want to See at the End](#c-what-we-want-to-see-at-the-end)
- [II. Datasets](#ii-datasets)
- [III. Methodology](#iii-methodology)
  - [A. System Architecture](#a-system-architecture)
  - [B. Feature Extraction](#b-feature-extraction)
  - [C. Risk Models](#c-risk-models)
  - [D. Four-Tier Adaptive Intervention](#d-four-tier-adaptive-intervention)
  - [E. Personalized Sensitivity](#e-personalized-sensitivity)
  - [F. Deployment on QNX](#f-deployment-on-qnx)
  - [G. Passenger-Facing Interfaces](#g-passenger-facing-interfaces)
- [IV. Evaluation & Analysis](#iv-evaluation--analysis)
- [V. Related Work](#v-related-work)
- [VI. Conclusion: Discussion](#vi-conclusion-discussion)
- [Team Roles](#team-roles)
- [Repository Structure](#repository-structure)
- [References](#references)

---

## Abstract

As electric and autonomous vehicles turn the car into a space for entertainment and relaxation, motion sickness is becoming a major obstacle to passenger comfort, especially for occupants focused on screens. Because motion sickness accumulates over time rather than depending only on instantaneous acceleration, the timing and type of intervention must be decided proactively. This project proposes a QNX-based embedded system, prototyped on a Raspberry Pi, that estimates motion sickness risk from vehicle acceleration and accumulated motion exposure by combining a mathematical motion sickness model with lightweight machine learning. Based on the estimated risk, passenger context, and individual sensitivity, the system selects among four interventions (airflow, acceleration guidance, ambient lighting, and visual motion cues) and coordinates in-vehicle and mobile devices through RF communication. The project aims to demonstrate the feasibility of proactive and personalized motion sickness intervention in an embedded vehicle environment.

**Keywords:** motion sickness, proactive intervention, QNX, embedded system, lightweight machine learning

---

## I. Introduction

### A. Motivation

Recent advances in electric vehicles (EVs), autonomous driving, and intelligent automotive technologies are changing the role of the automobile. Vehicles have traditionally served mainly as a means of transportation. As driving automation develops, however, passengers are expected to spend more time on non-driving activities such as watching videos, using personal devices, working, or relaxing. Cars are becoming personal spaces for entertainment and relaxation, which makes passenger comfort an increasingly important part of the in-vehicle user experience (UX).

**Motion sickness** is one of the major obstacles to this change. Unlike drivers, who can see upcoming changes in direction, acceleration, and deceleration, passengers often have little awareness of what the vehicle is about to do. The problem gets worse when passengers focus on smartphones or in-vehicle displays instead of the road. And as autonomous driving lets even would-be drivers do non-driving activities, motion sickness may come to affect every occupant.

The growing adoption of EVs adds further concerns. EVs have rapid torque response and regenerative braking, which produce acceleration and deceleration patterns different from those of conventional vehicles. Research has shown that stronger regenerative braking can increase motion sickness under certain driving conditions. So rather than relying only on driving behavior, we need a **proactive** system that identifies early signs of motion sickness and intervenes before symptoms get worse.

### B. Problem Statement

Motion sickness is commonly explained by **sensory conflict**: information from the vestibular system does not match visual information. When a passenger focuses on a smartphone in a moving vehicle, their vision is fixed on a stationary object and tells them they are not moving, while the vestibular system senses the acceleration, braking, and turning. This mismatch contributes to discomfort.

Motion sickness also does not depend only on instantaneous acceleration. Its severity **accumulates over time**, and how fast symptoms develop and recover varies widely between people. Detecting a sudden movement alone is therefore not enough. A better approach must consider accumulated motion exposure, exposure duration, and individual sensitivity.

Existing comfort features usually react to vehicle motion or offer one specific kind of assistance. They do not decide *when* an intervention is needed or *which* response is most appropriate. From the passenger's point of view:

- uncomfortable motion conditions should be detected **before** symptoms become severe;
- help should come **without interrupting** ongoing activities;
- intervention intensity and timing should **scale progressively** with the estimated risk.

This calls for an integrated system that collects vehicle motion data, assesses motion sickness risk, and coordinates several in-vehicle comfort devices, while staying reliable and safe within the vehicle's embedded computing environment.

### C. What We Want to See at the End

At the end of the project we want an integrated prototype that demonstrates the full pipeline:

1. **Data acquisition**: vehicle acceleration from an IMU or prerecorded signals
2. **AI inference**: motion sickness risk estimated on a Raspberry Pi running QNX
3. **Decision-making**: one of four interventions selected according to risk and passenger context
4. **Device control**: fan, RGB LEDs, and vehicle display driven automatically
5. **Mobile integration**: visual motion cues and manual override on the passenger's phone over RF

The main objective is to **demonstrate the feasibility of proactive, personalized, and automated motion sickness intervention in an embedded vehicle environment**. The selected dataset contains controlled one-axis motion stimuli without visual conditions or intervention outcomes. This project therefore focuses on validating **risk estimation and intervention triggers**, not on proving actual symptom reduction in real EVs.

---

## II. Datasets

We use the **TU Delft 2022 motion sickness dataset** for model development and evaluation. It contains repeated motion exposure experiments together with passenger-reported sickness levels.

> - Source / link and license
> - Number of participants and sessions
> - Motion stimulus: axis, frequency, amplitude, exposure duration, rest / re-exposure protocol
> - Recorded signals and sampling rate
> - Label: MISC (MIsery SCore) scale and how often it was reported
> - Class balance after defining our risk labels
> - Preprocessing steps (resampling, filtering, windowing, handling missing reports)

**Known limitations.** The dataset uses controlled **one-axis** motion stimuli. It has **no visual conditions** (e.g., screen use) and **no intervention outcomes**. As a result:

- screen-ON and screen-attention information are used as **activation conditions** for interventions, not as training features;
- the models are validated as risk estimators and trigger generators, not as proof that interventions reduce symptoms.

---

## III. Methodology

### A. System Architecture

The system is implemented on a **Raspberry Pi running QNX**, acting as a prototype vehicle-side controller. It is split into modular components so that sensors, models, and comfort functions can later be replaced or extended without redesigning the whole system:

```
 IMU / prerecorded signal
          │
          ▼
 ┌──────────────────┐   ┌───────────────────┐   ┌──────────────────────┐
 │ Data acquisition │──▶│ Feature extraction│──▶│ Model inference      │
 └──────────────────┘   │ RMS, MSDV, time,  │   │ Oman + LR / GBM      │
                        │ rest/re-exposure  │   └──────────┬───────────┘
                        └───────────────────┘              ▼
  Mobile app ◀──── RF (Wi-Fi/BT) ────┐        ┌──────────────────────┐
  (cues, override,                   ├────────│ Intervention decision│
   screen state)                     │        │ 4-tier logic         │
                                     │        └──────────┬───────────┘
                                     │                   ▼
                                     │        ┌──────────────────────┐
                                     └────────│ Device control       │
                                              │ Fan · RGB LED · Display│
                                              └──────────────────────┘
```

<!-- TODO: replace with a proper diagram image in docs/figures/ -->

### B. Feature Extraction

From the acceleration signal the system extracts features that capture both **recent motion** and **accumulated exposure**:

| Feature | Meaning |
|---|---|
| Acceleration RMS | Intensity of recent motion over a window |
| Motion Sickness Dose Value (MSDV) | Cumulative motion exposure, the standard dose metric for motion sickness |
| Exposure duration | How long the passenger has been exposed |
| Rest / re-exposure information | Captures recovery during rest and faster onset on re-exposure |


### C. Risk Models

We combine a **mathematical model** with **lightweight machine learning**:

- **Oman model.** A physiologically motivated model of how motion sickness accumulates and recovers over time. It provides a time-dependent sickness estimate and the basis for per-passenger sensitivity.
- **Logistic regression.** Used for initial (low-level) risk assessment. It is simple, interpretable, and trivial to implement in C.
- **Shallow Gradient Boosting Machine (GBM).** Used for higher-level risk estimation, where non-linear interactions between features matter more. Keeping the trees shallow keeps inference cheap and easy to export to C/C++.

**Why these models?** The target is an embedded RTOS, not a GPU server. Both models are small, fast, deterministic at inference time, and can be turned into plain C/C++ without a Python runtime. Deep models would add accuracy risk on a small dataset and deployment risk on QNX without a clear benefit.

### D. Four-Tier Adaptive Intervention

Interventions escalate from low-cost to high-cost. Higher tiers use **longer observation windows and stricter thresholds** to avoid unnecessary activation.

| Tier | Intervention | Decision method | Extra condition |
|---|---|---|---|
| 1 | **Airflow** (fan) | Oman model + logistic regression | — |
| 2 | **Acceleration guidance** | Rule-based acceleration analysis | — |
| 3 | **Ambient lighting** (RGB LEDs) | GBM risk assessment | Screen ON |
| 4 | **Visual motion cues** (display / phone) | GBM higher-risk assessment | Screen attention |

Passengers can **manually override** any automatic intervention. The system never controls steering, braking, or other safety-critical driving functions.

### E. Personalized Sensitivity

People differ widely in motion sickness susceptibility. In the Oman model this appears as a **sensitivity factor K** that scales how strongly motion exposure turns into sickness.

- **On the prototype**, K is fixed to a population value (K_pop) fitted on the TU Delft data, so the deployed features match the distribution the model was trained on.
- **Offline personalization study.** Using the TU Delft sessions, we fit a per-participant K from each participant's early MISC reports and measure how much it improves risk prediction for the rest of their session compared with K_pop. This shows how much individual sensitivity matters without requiring passenger input during the ride.
- **Future work:** feeding per-passenger feedback into K at runtime, once a suitable input channel is added.

### F. Deployment on QNX

Python-based ML libraries cannot be assumed to run on QNX, so training and execution are separated:

1. Train and validate models on a **development PC** (Python).
2. Export the trained inference logic (LR coefficients, GBM trees) to **QNX-compatible C/C++**.
3. Cross-compile for the Raspberry Pi target and deploy.
4. Run real-time inference, decision-making, and device control on QNX.
5. Verify that **PC and QNX inference results match** on the same inputs.

### G. Passenger-Facing Interfaces

- **Mobile app:** receives intervention commands from the controller, shows visual motion cues synchronized with vehicle movement, lets the passenger override interventions, and sends screen-state information (screen on, app in foreground) back.
- **Vehicle display UI:** shows visual motion cues, acceleration guidance, and current intervention status.
- **HUD:** a separate view of the vehicle screen app that shows the ambient acceleration-direction cue. It is drawn on a black background with an optional mirror mode, so a phone or tablet placed under a tilted acrylic sheet or the windshield works as a reflective HUD. The cue shows only the direction of vehicle acceleration, so it does not need to be registered to the road like an AR-HUD.
- **Communication:** bidirectional RF (Wi-Fi or Bluetooth) between controller and phones, with failure detection and a fallback over a wired connection to an external wireless router.


---

## IV. Evaluation & Analysis

**Evaluation plan.**

- **Participant-level cross-validation**: no participant appears in both training and test folds, which measures generalization to unseen passengers.
- **Classification metrics**: accuracy, precision, recall, F1, ROC-AUC for each risk model.
- **Baselines**: Oman model alone vs. Oman + LR vs. GBM.
- **Deployment consistency**: compare PC (Python) and QNX (C/C++) outputs on identical inputs.
- **System behavior**: intervention activation timeline on prerecorded drives (how often each tier fires, false activations).

---

## V. Related Work

**Commercial solutions.** Apple's **Vehicle Motion Cues** uses device sensors to detect vehicle motion and shows animated dots along the edges of the iPhone or iPad screen to reduce sensory conflict. Our system takes a **vehicle-centered** approach instead: a central controller evaluates risk and coordinates several interventions (airflow, guidance, lighting, cues), and it accounts for accumulated exposure and individual sensitivity rather than applying the same response to everyone.

**Research.** Irmak et al. studied how acceleration amplitude and exposure duration relate to motion sickness development in controlled motion experiments. Their findings show time-dependent accumulation and recovery dynamics and the importance of individual sensitivity, which supports combining the Oman model with lightweight ML.

**Tools and libraries used.**

> - Python, NumPy, pandas, scikit-learn (LR, GBM), matplotlib
> - Model export tool for GBM → C (if any)
> - QNX Software Development Platform, Raspberry Pi
> - IMU sensor model, fan / RGB LED drivers
> - Mobile app framework
> - Blogs / documentation consulted

---

## VI. Conclusion: Discussion

> **TODO (team):** summarize what the prototype achieved, key results from Section IV, and lessons learned.

**Limitations.**

- The training data uses one-axis, controlled lab motion, not real multi-axis driving.
- No visual conditions or intervention outcomes are in the data, so we validate risk estimation and triggers rather than actual symptom reduction.

**Future work: EV-specific mitigation.**

- Add EV-specific motion features such as jerk and the patterns caused by rapid torque response and regenerative braking, and refine models with real EV driving data.
- Combine camera-based analysis of head movement and visual attention with acceleration-based prediction in a **multimodal** model.
- Validate with real-world EV drives and passenger responses.

---

## Team Roles

| Name | Role | Responsibilities |
|---|---|---|
| Seunghee Kim | Back-end Developer, AI Developer | Trains LR and GBM risk models, evaluates them with participant-level CV, implements inference in QNX-compatible C/C++, integrates the four-tier intervention logic |
| Hyeokjin Lee | AI Developer, User / Customer | Defines passenger scenarios and validates requirements; preprocesses the TU Delft dataset, extracts features (RMS, MSDV), implements the Oman model and personalized sensitivity |
| Yookyung Jung | Back-end Developer, Server Manager | IMU data acquisition, fan / RGB LED / display control, RF communication, runtime environment, communication reliability and fallback |
| Junmin Lee | Front-end Developer, UI/UX Designer | Mobile app and vehicle display UI: visual motion cues, acceleration guidance, intervention status, HUD view, manual override |

---

## Repository Structure

```
.
├── README.md                # this blog
├── Makefile                 # make pc | make qnx | make deploy | make verify
│
├── core/                    # portable C++ shared by PC and QNX (no QNX headers)
│   ├── mmcore.hpp/.cpp      # preprocessing → Oman filter → features → logistic → tier logic
│   └── model_params.hpp     # filter + model coefficients (generated by training/export.py)
│
├── hub/                     # QNX daemon on Raspberry Pi 4
│   ├── main.cpp             # 100 Hz acquisition, 1 Hz inference, 10 Hz cues, --replay mode
│   ├── imu.cpp              # MPU-6050 over I2C (devctl)
│   ├── net.cpp              # TCP server for apps, line-delimited JSON
│   ├── fan_ctl.py           # fan GPIO control via stdin commands
│   └── run.sh               # start I2C driver and the hub
│
├── pc/
│   └── replay.cpp           # CSV → per-second features, risk, tiers (training table + verification)
│
├── training/                # Python, development PC
│   ├── build_table.py       # TU Delft → training table
│   ├── labels.py            # label design (horizon × MISC threshold)
│   ├── cv.py                # leave-one-participant-out CV, nested CV
│   ├── models/              # baselines, logistic (1A), GBM (1B), logistic + GBM residual (2)
│   ├── personalize.py       # offline per-participant sensitivity (K) study
│   ├── evaluate.py          # AUROC, PR-AUC, Brier, lead time, false alarms, Wilcoxon
│   ├── export.py            # selected model → core/model_params.hpp
│   ├── verify.py            # PC vs. QNX output consistency
│   └── notebooks/
│
├── app/
│   ├── mobile/              # phone: motion-dot cues, override, screen state
│   └── vehicle/             # in-vehicle screen app (?view=display | hud)
│       ├── shared/          # hub connection, message parsing, cue TTL
│       ├── display/         # vehicle display: dots, guidance, intervention status
│       └── hud/             # HUD: black background, direction gradient, mirror mode
│
├── data/                    # raw dataset linked, not committed (see data/README.md)
├── docs/
│   ├── protocol.md          # hub ↔ app message spec v1
│   └── figures/             # diagrams, plots, screenshots
└── results/                 # evaluation tables and figures
```

---
