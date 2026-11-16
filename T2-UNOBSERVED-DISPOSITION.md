# Disposition of unobserved selector records

These records did not structurally match in the expanded-selector snapshot union. They are inventoried below; **unobserved is not a verified defect**. Pseudo-elements are not queried by that snapshot tool. Source atom results are in [T2-UNOBSERVED-SOURCE.md](T2-UNOBSERVED-SOURCE.md); detailed source and live evidence is in [T2-INVENTORY.md](T2-INVENTORY.md) and `NOVA-PORT.md`. "Direct" means bounded separate evidence, not all branches, paints, or platforms.

## Record dispositions

| Records | Disposition and remaining work |
|---|---|
| `chrome/browser.css:18`, `chrome/browser.css:22`, `chrome/browser.css:40`, `chrome/browser.css:69`, `chrome/browser.css:78` | Direct split-panel pseudo-element computed-style and fullscreen samples; normal tabbox, all grouped branches, pseudo geometry, and paint remain open. |
| `chrome/browser.css:53` | Direct lightweight-theme selector and background controls; image themes and paint remain open. |
| `chrome/content/newtab.css:40` | Direct logo pseudo-content and bounded pixel samples; responsive overlap, clipping, and real RTL remain T3/T8. |
| `chrome/findbar.css:13`, `chrome/findbar.css:18` | Direct open-findbar pseudo content and hover computed styles; physical pointer and painted title remain open. |
| `chrome/icons.css:61` | Bitwarden content-image selector not exercised with the real extension; a synthetic toolbar fixture does not prove this content target. Carry to T4 when a suitable extension environment is available. |
| `chrome/icons.css:153`, `chrome/icons.css:161`, `chrome/icons.css:169`, `chrome/icons.css:206`, `chrome/icons.css:215`, `chrome/icons.css:223`, `chrome/icons.css:236` | Old `.menu-iconic-icon` descendants unobserved. Four synthetic extension menu items expose loaded internal `.menu-icon` nodes whose opacity remains 1 under modern-query replacement controls; Bitwarden and sidebar native-icon suppression still need individual and painted checks. |
| `chrome/icons.css:228`, `chrome/icons.css:241` | Fixed-hostname sidebar selectors do not match two synthetic sidebar actions in four profiles; profile-specific extension URLs were observed. Real extension and profile variations remain untested. |
| `chrome/icons.css:251` | Group contains pseudo-element and non-pseudo branches; sampled app-menu and zoom-host evidence does not prove every branch, conditional state, or painted icon. |
| `chrome/icons.css:275`, `chrome/icons.css:300` | Candidate IDs not textually found in searched tagged browser/toolkit source or sampled snapshots; other states, generated markup, and source outside those trees remain possible. |
| `chrome/icons.css:339` | Current app-menu zoom host and pseudo-content URL directly sampled and resource loaded; alternate ID, pseudo geometry, and paint remain open. |
| `chrome/icons.css:356`, `chrome/icons.css:359`, `chrome/icons.css:366`, `chrome/icons.css:369`, `chrome/icons.css:372`, `chrome/icons.css:375` | Account-menu selector states not verified with a signed-in account. Tagged source has references for some IDs; no credentials or account access assumed. Carry signed-in behavior to T4/T8. |
| `chrome/icons.css:476` | Direct Help-view samples find current `appMenu_help_reportBrokenSite`, not either old target; it is disabled on the local `about:robots` fixture. Reportable URLs and paint remain open. |
| `chrome/icons.css:524`, `chrome/icons.css:530` | Old menu child classes unobserved in sampled light DOM; direct inspection of one real item finds newer `.menu-icon` and `.menu-text` children. Other menus and painted spacing remain open. |
| `chrome/icons.css:541`, `chrome/icons.css:544`, `chrome/icons.css:547`, `chrome/icons.css:550`, `chrome/icons.css:553` | Verified Linux direct-child mismatch: real items are under `#context-navigation`; descendant controls work. Tagged markup has a separate macOS branch; a safe fix and macOS test remain open. |
| `chrome/icons.css:601`, `chrome/icons.css:723`, `chrome/icons.css:748` | No exact tagged browser/toolkit text and none in sampled link/page/input popup item lists. Other source trees, dynamic states, and other builds remain possible. |
| `chrome/menus.css:15`, `chrome/menus.css:16` | Sampled app-menu shadow root contains a content slot, not a `#shadow-root` ID; selector does not match sampled document or open root. Other versions and painted result remain open. |
| `chrome/menus.css:27` | Direct synthetic disabled-item hover reaches `_moz-menuactive` and a variable control; built-in disabled states and painted focus remain open. |
| `chrome/navbar.css:64`, `chrome/navbar.css:93` | Direct synthetic-pointer hover matches both toolbar branches and computed accent; focus branch, physical pointer, and painting remain open. |
| `chrome/navbar.css:68`, `chrome/navbar.css:76`, `chrome/navbar.css:97`, `chrome/navbar.css:105` | Direct toolbar `::before` color/content measurements accompany hover; pseudo geometry, all branches, and paint remain open. |
| `chrome/navbar.css:119` | Root lacked `tabsintitlebar` in sampled headless profiles; temporary attribute control matches and applies properties. Natural titlebar states and other platforms remain open. |
| `chrome/navbar.css:132` | Legacy sidebar media query was inactive in sampled profiles; modern-query title control follows preference. Painted title and actual titlebar states remain open. |
| `chrome/sidebar.css:14` | Direct `:root[lwtheme] sidebar-main` query in temporary themes and both Nova states; other themes and paint remain open. |
| `chrome/sidebar.css:27`, `chrome/sidebar.css:30`, `chrome/sidebar.css:41` | Direct sidebar-box pseudo-content and pseudo-class-locked hover samples; physical pointer and paint remain open. |
| `chrome/sidebar.css:59`, `chrome/sidebar.css:68` | Direct open-shadow wrapper pseudo-content and pseudo-class-locked hover samples; physical pointer and paint remain open. |
| `chrome/sidebar.css:82` | Rule identifies Floorp compatibility; not observed in sampled Firefox sidebar or tagged browser/toolkit source. Floorp and other states were not tested. |
| `chrome/tabs.css:12`, `chrome/tabs.css:20`, `chrome/tabs.css:42`, `chrome/tabs.css:46` | Direct vertical-strip and horizontal-toolbar pseudo-content/hover samples; focus, pseudo geometry, and paint remain open. |
| `chrome/tabs.css:67` | Normal/private adjacency selectors match only temporary preceding-button controls in sampled default layouts. Real customized adjacent items remain open. |
| `chrome/userChrome.css:30` | Direct visible URL-bar placeholder token intervention in both Nova states; other hosts and painted text remain open. |
