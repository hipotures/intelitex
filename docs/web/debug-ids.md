# UI debug identifiers

This reference table mirrors `web/src/debug/regions.ts`; a unit test checks every row.
IDs are stable UI-debug API.
The Debug toggle lives in Settings and is stored only as a browser UI preference.
A repeated component keeps its type ID; `data-entity-id` identifies the source,
workspace, profile, term or chapter instance separately. These codes are visible
only in Debug mode and are not product terminology.
On a workspace overview, Debug also displays the current workspace's route/API ID as
selectable text beside the header metadata. This is the value of `data-entity-id` on
`WSP`; a three-letter UI panel code is never a workspace ID.

| ID | Conceptual component | Source component/file |
| --- | --- | --- |
| HDR | Application header | `web/src/app/Shell.tsx` |
| BLS | Server connection loading screen | `web/src/app/Shell.tsx` |
| WRK | Whole Work home content region | `web/src/features/work/Home.tsx` |
| WHT | Work home title and description group | `web/src/features/work/Home.tsx` |
| ACT | Active workspaces | `web/src/features/work/Home.tsx` |
| WLS | Active workspace list | `web/src/features/work/Home.tsx` |
| WRC | Workspace row | `web/src/features/work/Home.tsx` |
| LIB | Library | `web/src/features/work/Home.tsx` |
| BKC | Library book card | `web/src/features/work/Home.tsx` |
| LSD | Library source details drawer | `web/src/features/work/LibrarySource.tsx` |
| LSI | Library source inspection section | `web/src/features/work/LibrarySource.tsx` |
| WCM | Workspace creation and setup modal | `web/src/features/work/LibrarySource.tsx` |
| WMP | Workspace setup pass-model group | `web/src/features/work/LibrarySource.tsx` |
| ARD | Archive drawer | `web/src/features/work/Home.tsx` |
| ARI | Archived workspace row | `web/src/features/work/Home.tsx` |
| WSP | Workspace overview | `web/src/features/pipeline/Workspace.tsx` |
| WHH | Workspace header | `web/src/features/pipeline/Workspace.tsx` |
| PHR | Workspace phase rail | `web/src/features/pipeline/Workspace.tsx` |
| PRC | Unprepared workspace card | `web/src/features/pipeline/Workspace.tsx` |
| STF | Sections toolbar and filters | `web/src/features/pipeline/Workspace.tsx` |
| SCT | Sections table card | `web/src/features/pipeline/Workspace.tsx` |
| PMA | Pipeline models accordion | `web/src/features/pipeline/Workspace.tsx` |
| MPA | Pipeline model · Pass 1 | `web/src/features/pipeline/Workspace.tsx` |
| MPB | Pipeline model · Pass 2 | `web/src/features/pipeline/Workspace.tsx` |
| MPC | Pipeline model · Pass 3 | `web/src/features/pipeline/Workspace.tsx` |
| MPD | Pipeline model · Pass 4 | `web/src/features/pipeline/Workspace.tsx` |
| MPE | Pipeline model · Pass 5 | `web/src/features/pipeline/Workspace.tsx` |
| LVA | Live activity accordion | `web/src/features/pipeline/Workspace.tsx` |
| RAL | Recent execution card | `web/src/features/pipeline/Workspace.tsx` |
| PUL | Publication log card | `web/src/features/pipeline/Workspace.tsx` |
| PVD | Section preview drawer | `web/src/features/pipeline/Workspace.tsx` |
| SMG | Section model override group | `web/src/features/pipeline/Workspace.tsx` |
| SMA | Section model override · Pass 1 | `web/src/features/pipeline/Workspace.tsx` |
| SMB | Section model override · Pass 2 | `web/src/features/pipeline/Workspace.tsx` |
| SMC | Section model override · Pass 3 | `web/src/features/pipeline/Workspace.tsx` |
| SMD | Section model override · Pass 4 | `web/src/features/pipeline/Workspace.tsx` |
| SME | Section model override · Pass 5 | `web/src/features/pipeline/Workspace.tsx` |
| ACM | Archive confirmation modal | `web/src/features/pipeline/Workspace.tsx` |
| REV | Review page | `web/src/features/review/Review.tsx` |
| RVH | Review heading and approval | `web/src/features/review/Review.tsx` |
| RVF | Review search and filters | `web/src/features/review/Review.tsx` |
| RVC | Review category filters | `web/src/features/review/Review.tsx` |
| RVB | Review visible-scope bulk action | `web/src/features/review/Review.tsx` |
| RVL | Review list and detail layout | `web/src/features/review/Review.tsx` |
| RFL | Review terminology list | `web/src/features/review/Review.tsx` |
| RVD | Review term detail | `web/src/features/review/Review.tsx` |
| RVE | Review evidence group | `web/src/features/review/Review.tsx` |
| RVM | Review navigation footer | `web/src/features/review/Review.tsx` |
| RUC | Unsaved Review form modal | `web/src/features/review/Review.tsx` |
| RBM | Bulk Review confirmation modal | `web/src/features/review/Review.tsx` |
| RDR | Reader page | `web/src/features/reader/Reader.tsx` |
| RSH | Reader shell | `web/src/features/reader/Reader.tsx` |
| RBL | Reader book list | `web/src/features/reader/Reader.tsx` |
| RBB | Reader book card | `web/src/features/reader/Reader.tsx` |
| RBC | Reader content region | `web/src/features/reader/Reader.tsx` |
| RCO | Reader chapter controls | `web/src/features/reader/Reader.tsx` |
| RPG | Reader translated chapter | `web/src/features/reader/Reader.tsx` |
| RSL | Reader text selection actions | `web/src/features/reader/Reader.tsx` |
| RMK | Reader markers accordion | `web/src/features/reader/Reader.tsx` |
| RCM | Reader context modal | `web/src/features/reader/Reader.tsx` |
| PHD | Phase detail page | `web/src/features/pipeline/Phase.tsx` |
| PLD | Phase loading card | `web/src/features/pipeline/Phase.tsx` |
| PHH | Phase detail heading | `web/src/features/pipeline/Phase.tsx` |
| PHM | Phase metrics group | `web/src/features/pipeline/Phase.tsx` |
| PMT | Phase metric card | `web/src/features/pipeline/Phase.tsx` |
| PSR | Prepare source structure card | `web/src/features/pipeline/PrepareContent.tsx` |
| PPR | Prepare source preview card | `web/src/features/pipeline/PrepareContent.tsx` |
| PSM | Prepare source metadata card | `web/src/features/pipeline/PrepareContent.tsx` |
| PCK | Prepare checks card | `web/src/features/pipeline/PrepareContent.tsx` |
| RPM | Prepare rebuild confirmation modal | `web/src/features/pipeline/Phase.tsx` |
| PAN | Analyse units card | `web/src/features/pipeline/AnalyseContent.tsx` |
| PUC | Analyse P1 costs and token usage card | `web/src/features/pipeline/AnalyseContent.tsx` |
| PAV | Analyse P1 source and result preview | `web/src/features/pipeline/AnalyseContent.tsx` |
| AUM | Analyse unit run confirmation | `web/src/features/pipeline/AnalyseContent.tsx` |
| PAE | Analyse empty-state card | `web/src/features/pipeline/Phase.tsx` |
| PAC | Analyse P1 data action card | `web/src/features/pipeline/Phase.tsx` |
| PRM | P1 reset confirmation modal | `web/src/features/pipeline/Phase.tsx` |
| PTS | Translate pass summary card | `web/src/features/pipeline/TranslateContent.tsx` |
| PSC | Translate chunk progress card | `web/src/features/pipeline/TranslateContent.tsx` |
| TPV | Translate pass source and result preview | `web/src/features/pipeline/TranslateContent.tsx` |
| TCM | Translate targeted pass confirmation | `web/src/features/pipeline/TranslateContent.tsx` |
| PDG | Translate diagnostics card | `web/src/features/pipeline/TranslateContent.tsx` |
| PPO | Publish output card | `web/src/features/pipeline/Phase.tsx` |
| PSE | Publish section selection card | `web/src/features/pipeline/PublishSelection.tsx` |
| PSP | Publish section source preview | `web/src/features/pipeline/PublishSelection.tsx` |
| PPV | Publish validation card | `web/src/features/pipeline/Phase.tsx` |
| PMD | Publish metadata card | `web/src/features/pipeline/Phase.tsx` |
| SET | Settings modal | `web/src/app/Shell.tsx` |
| STB | Settings tabs | `web/src/app/Shell.tsx` |
| SPA | Settings · Paths | `web/src/app/Shell.tsx` |
| SPM | Settings · Models | `web/src/app/Shell.tsx` |
| MPR | Settings model profile row | `web/src/app/Shell.tsx` |
| SPI | Settings · Interface | `web/src/app/Shell.tsx` |
| SPD | Settings · Debug | `web/src/app/Shell.tsx` |

The workspace creation/setup modal uses the registered `WCM` ID.
