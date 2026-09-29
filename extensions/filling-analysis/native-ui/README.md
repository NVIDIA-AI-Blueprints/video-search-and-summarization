# Native VSS Filling UI

Builds the existing VSS UI from its lockfile with the Filling tab and native chat context/artifacts. See [the QA recipe](../deployment/qa/README.md).

Build variables: VSS_UI_BASE_IMAGE, VSS_FILLING_UI_IMAGE, VSS_FILLING_UI_BUILDER_IMAGE. The default base is registry-addressable and pinned. NEXT_PUBLIC_ENABLE_FILLING_TAB controls visibility. /filling/ routes to the API; hiding it does not clear state.
