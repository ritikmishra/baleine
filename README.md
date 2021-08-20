# bookish_octo_lamp

This contains the scripts that I use to help me play Anacreon more efficiently.

[Anacreon](https://anacreon.kronosaur.com/) is a 4X game made by [Kronosaur Productions, LLC](https://kronosaur.com/).

See [`anacreonlib`](https://github.com/ritikmishra/anacreonlib) for the underlying
library that makes it easy to interact with the game's API.


This script set includes a web dashboard. This dashboard has 2 primary features
- Automatic generation of HTML forms based on Python function type signatures
  - Essentially, as long as you appropriately add type annotations to your 
    function signatures, we will be able to generate an HTML form for it, so
    that it can be called through a web interface
- Diagnostic heatmaps
  - These heatmaps let you see at a glance which worlds are have resource production
    surpluses/deficits. They also let you see where resources are being stockpiled
    in your empire.

The dashboard is written in pure Python using minimal client-side JS. 
