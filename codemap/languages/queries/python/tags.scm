;; codemap T1 tags for Python. See languages/registry.py for the capture contract.

;; --- definitions -------------------------------------------------------------

(function_definition
  name: (identifier) @name
  parameters: (parameters) @params
  return_type: (_)? @returns) @definition.function

(class_definition
  name: (identifier) @name
  superclasses: (argument_list)? @params) @definition.class

;; --- docstrings (first statement of the body) -------------------------------
;; grammar versions differ on whether a bare body string is wrapped.

(function_definition body: (block . (expression_statement (string) @docstring)))
(function_definition body: (block . (string) @docstring))
(class_definition    body: (block . (expression_statement (string) @docstring)))
(class_definition    body: (block . (string) @docstring))

;; --- decorators ------------------------------------------------------------

(decorator) @decorator

;; --- references (call sites, by name) ------------------------------------

(call function: (identifier) @name) @reference.call
(call function: (attribute attribute: (identifier) @name)) @reference.call

;; --- imports (raw statement text is kept verbatim) ---------------------

(import_statement) @import
(import_from_statement) @import
(future_import_statement) @import
