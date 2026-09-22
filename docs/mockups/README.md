# Intelitex web reference mockup

This directory contains the pre-implementation visual reference for the Intelitex web UI.

## Reference

- `intelitex_workspace_mockup_v32.html`

The file is a self-contained demonstrator representing the latest agreed v32 workspace design state before production web implementation.

The original v32 artifact was created outside the repository. This checked-in copy is a reconstructed reference based on that agreed design state and is intentionally kept separate from production frontend code.

## Purpose

Use the mockup to compare future implementation changes against the agreed interaction and visual structure. It is a design/UX reference, not an API contract and not production code.

Important reference behavior includes:

- workspace-first navigation with **Work** and **Reader**;
- active workspaces and a separate library;
- no dedicated Pause control;
- missing-cover fallback based on title initials, maximum three characters;
- workflow order **Prepare → Analyse → Review → Translate → Publish**;
- no "P1" prefix in Analyse or Review labels;
- in this v32 state, **Review** is the only workflow tile presented as a navigation target;
- Analyse is global before Review;
- P2-P5 translation progress is section-based after Review;
- excluded sections are visually greyed out;
- section status uses compact icons rather than duplicated textual state labels;
- Review is a dedicated working view with intersecting status/category filters and evidence;
- Reader progress refers to currently available verified P5 text, not the untranslated remainder of the book.

Future phase-detail pages discussed after v32 are intentionally not added to this reference file.

## Scope

Do not import this HTML into production code.

The production frontend may use React, TypeScript, Vite, Tailwind, shadcn/ui and TanStack tooling, but it should preserve the accepted information architecture and interaction semantics unless a later design decision explicitly supersedes this reference.
