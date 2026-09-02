;; codemap T1 tags for Rust. See languages/registry.py for the capture contract.

;; --- definitions -------------------------------------------------------------
;; `impl Type { fn ... }` bodies are separate top-level declarations from the
;; struct/trait they implement (not byte-range nested inside them), so
;; parsing.py's generic function->method promotion never fires here — every
;; function_item captures as kind="function", impl methods included. A known
;; simplification: a second, impl-scoped `@definition.method` pattern would
;; double-capture every impl method (tree-sitter matches overlapping patterns
;; independently), so this stays a single pattern rather than risk that.

(function_item
  name: (identifier) @name
  parameters: (parameters) @params
  return_type: (_)? @returns) @definition.function

(struct_item name: (type_identifier) @name) @definition.class
(trait_item  name: (type_identifier) @name) @definition.interface
(enum_item   name: (type_identifier) @name) @definition.class

;; --- references (call sites, by name) ------------------------------------

(call_expression function: (identifier) @name) @reference.call
(call_expression
  function: (field_expression field: (field_identifier) @name)) @reference.call

;; --- imports ---------------------------------------------------------------

(use_declaration) @import
