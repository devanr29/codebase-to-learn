;; codemap T1 tags for JavaScript / JSX. See languages/registry.py for the contract.

;; --- definitions -------------------------------------------------------------

(function_declaration
  name: (identifier) @name
  parameters: (formal_parameters) @params) @definition.function

(generator_function_declaration
  name: (identifier) @name
  parameters: (formal_parameters) @params) @definition.function

(variable_declarator
  name: (identifier) @name
  value: (arrow_function parameters: (formal_parameters) @params)) @definition.function
(variable_declarator
  name: (identifier) @name
  value: (function_expression parameters: (formal_parameters) @params)) @definition.function

;; a function handed straight to a wrapper is still a named function:
;; `const getX = asyncHandler(async (req, res) => ...)`, `memo(() => ...)`,
;; `forwardRef(function (props, ref) {...})`. Timers and value hooks run a
;; callback but don't *define* one, and lazy()/loadable()/dynamic() wrap an
;; import() of another file's component, so they're excluded.
(variable_declarator
  name: (identifier) @name
  value: (call_expression
    function: (_) @_wrapper
    arguments: (arguments . (arrow_function parameters: (formal_parameters) @params)))
  (#not-match? @_wrapper "^(setTimeout|setInterval|setImmediate|requestAnimationFrame|useMemo|useState|useRef|useReducer|useEffect|useLayoutEffect|queueMicrotask|lazy|React.lazy|loadable|dynamic)$")) @definition.function
(variable_declarator
  name: (identifier) @name
  value: (call_expression
    function: (_) @_wrapper
    arguments: (arguments . (function_expression parameters: (formal_parameters) @params)))
  (#not-match? @_wrapper "^(setTimeout|setInterval|setImmediate|requestAnimationFrame|useMemo|useState|useRef|useReducer|useEffect|useLayoutEffect|queueMicrotask|lazy|React.lazy|loadable|dynamic)$")) @definition.function

(method_definition
  name: (property_identifier) @name
  parameters: (formal_parameters) @params) @definition.method

(class_declaration name: (identifier) @name) @definition.class

;; --- decorators ------------------------------------------------------------

(decorator) @decorator

;; --- references (call sites, by name) ------------------------------------

(call_expression function: (identifier) @name) @reference.call
(call_expression
  function: (member_expression property: (property_identifier) @name)) @reference.call

;; --- JSX component references (capitalized names only; filtered in
;;     parsing.py — intrinsic host elements like <div> aren't components).
;;     The plain `javascript` grammar parses JSX natively, so .jsx needs no
;;     separate query_dir the way tsx did against `typescript`. --------------

(jsx_opening_element      name: (identifier) @name) @reference.render
(jsx_self_closing_element name: (identifier) @name) @reference.render
(jsx_opening_element
  name: (member_expression property: (property_identifier) @name)) @reference.render
(jsx_self_closing_element
  name: (member_expression property: (property_identifier) @name)) @reference.render

;; --- imports -----------------------------------------------------------------

(import_statement) @import
(export_statement source: (string)) @import
