# Feature Wishlist

## 1. Automatically generated form modals for tasks that take parameters

### The end user experience
1. In the browser, I click on a button
2. The button causes a modal to pop up. It is a vertically arranged **form** that lets me enter in the parameters to the function
   1. The form is **type-safe**. It infers the **types**, **labels**, and **name attributes** from the function signature
   2. So, a task that takes a `ship_qty: int` will get rendered as a `<label>ship qty: <input type="number" step="1" name="ship_qty" /></label>` 
   3. A task that takes a `dry_run: bool` will get rendered as a `<label>dry run: <input type="checkbox" name="dry_run" /></label>`
   4. (Extended) There should be special handling of special blessed newtypes
      1. When anacreonlib gets newtypes for ship ids, fleet ids, etc, they should get converted into dropdowns for acceptable values.
         For example, a `FleetId` newtype should be a dropdown of all the fleets that we own that are deployed, etc
      2. a
   




