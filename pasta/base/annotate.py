# coding=utf-8
"""Annotate python syntax trees with formatting from the source file."""
# Copyright 2017 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import abc
import ast
import contextlib
import itertools
import six
from six.moves import zip

from pasta.base import ast_constants
from pasta.base import ast_utils
from pasta.base import token_generator


# ==============================================================================
# == Helper functions for decorating nodes with prefix + suffix               ==
# ==============================================================================

def _gen_wrapper(f, scope=True, prefix=True, suffix=True,
                 max_suffix_lines=None):
  @contextlib.wraps(f)
  def wrapped(self, node, *args, **kwargs):
    with (self.scope(node) if scope else _noop_context()):
      if prefix:
        self.prefix(node)
      f(self, node, *args, **kwargs)
      if suffix:
        self.suffix(node, max_lines=max_suffix_lines)
  return wrapped


@contextlib.contextmanager
def _noop_context():
  yield


def expression(f):
  """Decorates a function where the node is an expression."""
  return _gen_wrapper(f, max_suffix_lines=0)


def space_around(f):
  """Decorates a function where the node has whitespace prefix and suffix."""
  return _gen_wrapper(f, scope=False)


def space_left(f):
  """Decorates a function where the node has whitespace prefix."""
  return _gen_wrapper(f, scope=False, suffix=False)


def statement(f):
  """Decorates a function where the node is a statement."""
  return _gen_wrapper(f, scope=False, max_suffix_lines=1)


def block_statement(f):
  """Decorates a function where the node is a statement with children."""
  return _gen_wrapper(f, suffix=False, scope=False)


# ==============================================================================
# == NodeVisitors for annotating an AST                                       ==
# ==============================================================================

class BaseVisitor(ast.NodeVisitor):
  """Walks a syntax tree in the order it appears in code.

  This class has a dual-purpose. It is implemented (in this file) for annotating
  an AST with formatting information needed to reconstruct the source code, but
  it also is implemented in pasta.base.codegen to reconstruct the source code.

  Each visit method in this class specifies the order in which both child nodes
  and syntax tokens appear, plus where to account for whitespace, commas,
  parentheses, etc.
  """

  __metaclass__ = abc.ABCMeta

  def __init__(self):
    self._stack = []

  def visit(self, node):
    self._stack.append(node)
    ast_utils.setup_props(node)
    super(BaseVisitor, self).visit(node)
    assert node is self._stack.pop()

  def prefix(self, node):
    """Account for some amount of whitespace as the prefix to a node."""
    self.attr(node, 'prefix', [self.ws])

  def suffix(self, node, max_lines=None):
    """Account for some amount of whitespace as the suffix to a node."""
    self.attr(node, 'suffix', [lambda: self.ws(max_lines=max_lines)])

  def indented(self, node, children_attr):
    for child in getattr(node, children_attr):
      yield child

  @contextlib.contextmanager
  def scope(self, node):
    """Context manager to handle a parenthesized scope."""
    yield

  def token(self, token_val):
    """Account for a specific token."""

  def attr(self, node, attr_name, attr_vals, deps=None, default=None):
    """Handles an attribute on the given node."""

  def ws(self, max_lines=None):
    """Account for some amount of whitespace.

    Arguments:
      max_lines: (int) Maximum number of newlines to consider.
    """
    return ''

  def optional_token(self, node, attr_name, token_val):
    """Account for a suffix that may or may not occur."""

  def ws_oneline(self):
    """Account for up to one line of whitespace."""
    return self.ws(max_lines=1)

  # ============================================================================
  # == BLOCK STATEMENTS: Statements that contain a list of statements         ==
  # ============================================================================

  @block_statement
  def visit_Module(self, node):
    self.generic_visit(node)
    self.attr(node, 'suffix', [self.ws])

  @block_statement
  def visit_If(self, node):
    tok = 'elif' if ast_utils.prop(node, 'is_elif') else 'if'
    self.attr(node, 'open_if', [tok, self.ws], default=tok + ' ')
    self.visit(node.test)
    self.attr(node, 'open_block', [self.ws, ':', self.ws_oneline],
              default=':\n')

    for stmt in self.indented(node, 'body'):
      self.visit(stmt)

    if node.orelse:
      if (len(node.orelse) == 1 and isinstance(node.orelse[0], ast.If) and
          self.check_is_elif(node.orelse[0])):
        ast_utils.setprop(node.orelse[0], 'is_elif', True)
        self.visit(node.orelse[0])
      else:
        self.attr(node, 'elseprefix', [self.ws])
        self.token('else')
        self.attr(node, 'open_else', [self.ws, ':', self.ws_oneline],
                  default=':\n')
        for stmt in self.indented(node, 'orelse'):
          self.visit(stmt)

  @abc.abstractmethod
  def check_is_elif(self, node):
    """Return True if the node continues a previous `if` statement as `elif`.

    In python 2.x, `elif` statments get parsed as If nodes. E.g, the following
    two syntax forms are indistinguishable in the ast in python 2.

    if a:
      do_something()
    elif b:
      do_something_else()

    if a:
      do_something()
    else:
      if b:
        do_something_else()

    This method should return True for the 'if b' node if it has the first form.
    """

  @block_statement
  def visit_While(self, node):
    self.attr(node, 'while_keyword', ['while', self.ws], default='while ')
    self.visit(node.test)
    self.attr(node, 'open_block', [self.ws, ':', self.ws_oneline],
              default=':\n')
    for stmt in self.indented(node, 'body'):
      self.visit(stmt)

    if node.orelse:
      self.attr(node, 'else', [self.ws, 'else', self.ws, ':', self.ws_oneline],
                default=':\n')
      for stmt in self.indented(node, 'orelse'):
        self.visit(stmt)

  @block_statement
  def visit_For(self, node):
    self.attr(node, 'for_keyword', ['for', self.ws], default='for ')
    self.visit(node.target)
    self.attr(node, 'for_in', [self.ws, 'in', self.ws], default=' in ')
    self.visit(node.iter)
    self.attr(node, 'open_block', [self.ws, ':', self.ws_oneline],
              default=':\n')
    for stmt in self.indented(node, 'body'):
        self.visit(stmt)

    if node.orelse:
      self.attr(node, 'else', [self.ws, 'else', self.ws, ':', self.ws_oneline],
                default=':\n')

      for stmt in self.indented(node, 'orelse'):
        self.visit(stmt)

  @block_statement
  def visit_With(self, node):
    if hasattr(node, 'items'):
      return self.visit_With_3(node)
    if not getattr(node, 'is_continued', False):
      self.attr(node, 'with', ['with', self.ws], default='with ')
    self.visit(node.context_expr)
    if node.optional_vars:
      self.attr(node, 'with_as', [self.ws, 'as', self.ws], default=' as ')
      self.visit(node.optional_vars)

    if len(node.body) == 1 and self.check_is_continued_with(node.body[0]):
      node.body[0].is_continued = True
      self.attr(node, 'with_comma', [self.ws, ',', self.ws], default=', ')
    else:
      self.attr(node, 'open_block', [self.ws, ':', self.ws_oneline],
                default=':\n')
    for stmt in self.indented(node, 'body'):
      self.visit(stmt)

  @abc.abstractmethod
  def check_is_continued_with(self, node):
    """Return True if the node continues a previous `with` statement.

    In python 2.x, `with` statments with many context expressions get parsed as
    a tree of With nodes. E.g, the following two syntax forms are
    indistinguishable in the ast in python 2.

    with a, b, c:
      do_something()

    with a:
      with b:
        with c:
          do_something()

    This method should return True for the `with b` and `with c` nodes.
    """

  def visit_With_3(self, node):
    self.token('with')

    for i, withitem in enumerate(node.items):
      self.visit(withitem)
      if i != len(node.items) - 1:
        self.token(',')

    self.attr(node, 'with_body_open', [':', self.ws_oneline], default=':\n')
    for stmt in self.indented(node, 'body'):
      self.visit(stmt)

  @space_around
  def visit_withitem(self, node):
    self.visit(node.context_expr)
    if node.optional_vars:
      self.attr(node, 'as', [self.ws, 'as', self.ws], default=' as ')
      self.visit(node.optional_vars)

  @block_statement
  def visit_ClassDef(self, node):
    for i, decorator in enumerate(node.decorator_list):
      self.attr(node, 'decorator_prefix_%d' % i, [self.ws, '@'], default='@')
      self.visit(decorator)
      self.attr(node, 'decorator_suffix_%d' % i, [self.ws], default='\n')
    self.attr(node, 'class_def', ['class', self.ws, node.name, self.ws],
              default='class %s' % node.name, deps=('name',))
    if node.bases:
      self.token('(')
    else:
      self.optional_token(node, 'open_bases', '(')
    for i, base in enumerate(node.bases):
      self.visit(base)
      self.attr(node, 'base_suffix_%d' % i, [self.ws])
      if base != node.bases[-1]:
        self.token(',')
    if node.bases:
      self.optional_token(node, 'bases_extracomma', ',')
      self.token(')')
    else:
      self.optional_token(node, 'close_bases', ')')
    self.attr(node, 'open_block', [self.ws, ':', self.ws_oneline],
              default=':\n')
    for stmt in self.indented(node, 'body'):
      self.visit(stmt)

  @block_statement
  def visit_FunctionDef(self, node):
    for i, decorator in enumerate(node.decorator_list):
      self.attr(node, 'decorator_symbol_%d' % i, [self.ws, '@', self.ws],
                default='@')
      self.visit(decorator)
      self.attr(node, 'decorator_suffix_%d' % i, [self.ws_oneline],
                default='\n')
    self.attr(node, 'function_def',
              [self.ws, 'def', self.ws, node.name, self.ws, '('],
              deps=('name',), default='def %s(' % node.name)
    self.visit(node.args)
    self.attr(node, 'function_def_close', [self.ws, ')', self.ws], default=')')

    if getattr(node, 'returns', None):
      self.attr(node, 'returns_prefix', [self.ws, '->', self.ws],
                deps=('returns',), default=' -> ')
      self.visit(node.returns)

    self.attr(node, 'open_block', [self.ws, ':', self.ws_oneline],
              default=':\n')
    for stmt in self.indented(node, 'body'):
      self.visit(stmt)

  @block_statement
  def visit_TryFinally(self, node):
    # Try with except and finally is a TryFinally with the first statement as a
    # TryExcept in Python2
    if not isinstance(node.body[0], ast.TryExcept):
      self.attr(node, 'open_try', ['try', self.ws, ':', self.ws_oneline],
                default='try:\n')
    for stmt in node.body:
      self.visit(stmt)
    self.attr(node, 'open_finally',
              [self.ws, 'finally', self.ws, ':', self.ws_oneline],
              default='finally:\n')
    for stmt in self.indented(node, 'finalbody'):
      self.visit(stmt)

  @block_statement
  def visit_TryExcept(self, node):
    self.attr(node, 'open_try', ['try', self.ws, ':', self.ws_oneline],
              default='try:\n')
    for stmt in node.body:
      self.visit(stmt)
    for handler in node.handlers:
      self.visit(handler)
    if node.orelse:
      self.attr(node, 'open_else',
                [self.ws, 'else', self.ws, ':', self.ws_oneline],
                default='else:\n')
      for stmt in self.indented(node, 'orelse'):
        self.visit(stmt)

  @block_statement
  def visit_Try(self, node):
    # Python 3
    self.attr(node, 'open_try', [self.ws, 'try', self.ws, ':', self.ws_oneline],
              default='try:\n')
    for stmt in node.body:
      self.visit(stmt)
    for handler in node.handlers:
      self.visit(handler)
    if node.orelse:
      self.attr(node, 'open_else',
                [self.ws, 'else', self.ws, ':', self.ws_oneline],
                default='else:\n')
      for stmt in self.indented(node, 'orelse'):
        self.visit(stmt)
    if node.finalbody:
      self.attr(node, 'open_finally',
                [self.ws, 'finally', self.ws, ':', self.ws_oneline],
                default='finally:\n')
      for stmt in self.indented(node, 'finalbody'):
        self.visit(stmt)

  @block_statement
  def visit_ExceptHandler(self, node):
    self.token('except')
    if node.type:
      self.visit(node.type)
    if node.type and node.name:
      self.attr(node, 'as', [self.ws, 'as', self.ws], default=' as ')
    if node.name:
      if isinstance(node.name, ast.AST):
        self.visit(node.name)
      else:
        self.token(node.name)
    self.attr(node, 'open_block', [self.ws, ':', self.ws_oneline],
              default=':\n')
    for stmt in self.indented(node, 'body'):
      self.visit(stmt)

  @statement
  def visit_Raise(self, node):
    if hasattr(node, 'cause'):
      return self.visit_Raise_3(node)

    self.token('raise')
    if node.type:
      self.visit(node.type)
    if node.inst:
      self.attr(node, 'inst_prefix', [self.ws, ',', self.ws], default=', ')
      self.visit(node.inst)
    if node.tback:
      self.attr(node, 'tback_prefix', [self.ws, ',', self.ws], default=', ')
      self.visit(node.tback)

  def visit_Raise_3(self, node):
    if node.exc:
      self.attr(node, 'open_raise', ['raise', self.ws], default='raise ')
      self.visit(node.exc)
      if node.cause:
        self.attr(node, 'cause_prefix', [self.ws, 'from', self.ws],
                  default=' from ')
        self.visit(node.cause)
    else:
      self.token('raise')

  # ============================================================================
  # == STATEMENTS: Instructions without a return value                        ==
  # ============================================================================

  @statement
  def visit_Assert(self, node):
    self.token('assert')
    self.visit(node.test)
    if node.msg:
      self.token(',')
      self.visit(node.msg)

  @statement
  def visit_Assign(self, node):
    for i, target in enumerate(node.targets):
      self.visit(target)
      self.attr(node, 'equal_%d' % i, [self.ws, '=', self.ws], default=' = ')
    self.visit(node.value)

  @statement
  def visit_AugAssign(self, node):
    self.visit(node.target)
    op_token = '%s=' % ast_constants.NODE_TYPE_TO_TOKENS[type(node.op)][0]
    self.attr(node, 'operator', [self.ws, op_token, self.ws],
              default=' %s ' % op_token)
    self.visit(node.value)

  @statement
  def visit_Break(self, node):
    self.token('break')

  @statement
  def visit_Continue(self, node):
    self.token('continue')

  @statement
  def visit_Delete(self, node):
    self.attr(node, 'del', ['del', self.ws], default='del ')
    for i, target in enumerate(node.targets):
      self.visit(target)
      if target is not node.targets[-1]:
        self.attr(node, 'comma_%d' % i, [self.ws, ',', self.ws], default=', ')

  @statement
  def visit_Exec(self, node):
    self.attr(node, 'exec', ['exec', self.ws], default='exec ')
    self.visit(node.body)
    if node.globals:
      self.attr(node, 'in_globals', [self.ws, 'in', self.ws], default=' in ')
      self.visit(node.globals)
      if node.locals:
        self.attr(node, 'in_locals', [self.ws, ',', self.ws], default=', ')
        self.visit(node.locals)

  @statement
  def visit_Expr(self, node):
    self.visit(node.value)

  @statement
  def visit_Global(self, node):
    self.token('global')
    identifiers = []
    for ident in node.names:
      if ident != node.names[0]:
        identifiers.extend([self.ws, ','])
      identifiers.extend([self.ws, ident])
    self.attr(node, 'names', identifiers)

  @statement
  def visit_Import(self, node):
    self.attr(node, 'open_import', ['import', self.ws], default='import ')
    for i, alias in enumerate(node.names):
      self.visit(alias)
      if alias != node.names[-1]:
        self.attr(node, 'alias_sep_%d' % i, [self.ws, ',', self.ws],
                  default=', ')

  @statement
  def visit_ImportFrom(self, node):
    self.token('from')
    self.attr(node, 'module_prefix', [self.ws], default=' ')

    module_pattern = ['.', self.ws] * node.level
    if node.module:
      parts = node.module.split('.')
      for part in parts[:-1]:
        module_pattern += [self.ws, part, self.ws, '.']
      module_pattern += [self.ws, parts[-1]]

    self.attr(node, 'module', module_pattern,
              deps=('level', 'module'),
              default='.' * node.level + (node.module or ''))
    self.attr(node, 'module_suffix', [self.ws], default=' ')

    self.token('import')
    for alias in node.names:
      self.visit(alias)
      if alias != node.names[-1]:
        self.token(',')

  @statement
  def visit_Nonlocal(self, node):
    self.token('nonlocal')
    identifiers = []
    for ident in node.names:
      if ident != node.names[0]:
        identifiers.extend([self.ws, ','])
      identifiers.extend([self.ws, ident])
    self.attr(node, 'names', identifiers)

  @statement
  def visit_Pass(self, node):
    self.token('pass')

  @statement
  def visit_Print(self, node):
    self.attr(node, 'print_open', ['print', self.ws], default='print ')
    if node.dest:
      self.attr(node, 'redirection', ['>>', self.ws], default='>>')
      self.visit(node.dest)
      if node.values:
        self.attr(node, 'values_prefix', [self.ws, ',', self.ws], default=', ')
      elif not node.nl:
        self.attr(node, 'trailing_comma', [self.ws, ','], default=',')

    for i, value in enumerate(node.values):
      self.visit(value)
      if value is not node.values[-1]:
        self.attr(node, 'comma_%d' % i, [self.ws, ',', self.ws], default=', ')
      elif not node.nl:
        self.attr(node, 'trailing_comma', [self.ws, ','], default=',')

  @statement
  def visit_Return(self, node):
    self.token('return')
    if node.value:
      self.visit(node.value)

  @statement
  def visit_Yield(self, node):
    self.token('yield')
    if node.value:
      self.visit(node.value)

  # ============================================================================
  # == EXPRESSIONS: Anything that evaluates and can be in parens              ==
  # ============================================================================

  @expression
  def visit_Attribute(self, node):
    self.visit(node.value)
    self.attr(node, 'dot', [self.ws, '.', self.ws], default='.')
    self.token(node.attr)

  @expression
  def visit_BinOp(self, node):
    self.visit(node.left)
    self.visit(node.op)
    self.visit(node.right)

  @expression
  def visit_BoolOp(self, node):
    op_symbol = ast_constants.NODE_TYPE_TO_TOKENS[type(node.op)][0]
    for i, value in enumerate(node.values):
      self.visit(value)
      if value is not node.values[-1]:
        self.attr(node, 'op_%d' % i, [self.ws, op_symbol, self.ws],
                  default=' %s ' % op_symbol, deps=('op',))

  @expression
  def visit_Call(self, node):
    self.visit(node.func)
    self.attr(node, 'open_call', [self.ws, '(', self.ws], default='(')
    num_items = (len(node.args) + len(node.keywords) +
                 (1 if node.starargs else 0) + (1 if node.kwargs else 0))

    i = 0
    for arg in node.args:
      self.visit(arg)
      if i < num_items - 1:
        self.attr(node, 'comma_%d' % i, [self.ws, ',', self.ws], default=', ')
      i += 1

    starargs_idx = ast_utils.find_starargs(node)
    kw_end = len(node.args) + len(node.keywords) + (1 if node.starargs else 0)
    kw_idx = 0
    while i < kw_end:
      if i == starargs_idx:
        self.attr(node, 'starargs_prefix', [self.ws, '*'], default='*')
        self.visit(node.starargs)
      else:
        self.visit(node.keywords[kw_idx])
        kw_idx += 1
      if i < num_items - 1:
        self.attr(node, 'comma_%d' % i, [self.ws, ',', self.ws], default=', ')
      i += 1

    if node.kwargs:
      self.attr(node, 'kwargs_prefix', [self.ws, '**', self.ws], default='**')
      self.visit(node.kwargs)

    self.attr(node, 'arguments_suffix', [self.ws], default='')
    if num_items > 0:
      self.optional_token(node, 'extracomma', ',')

    self.attr(node, 'close_call', [self.ws, ')'], default=')')

  @expression
  def visit_Compare(self, node):
    self.visit(node.left)
    for op, comparator in zip(node.ops, node.comparators):
      self.visit(op)
      self.visit(comparator)

  @expression
  def visit_Dict(self, node):
    self.token('{')

    for i, key, value in zip(range(len(node.keys)), node.keys, node.values):
      self.visit(key)
      self.attr(node, 'key_val_sep_%d' % i, [self.ws, ':', self.ws],
                default=': ')
      self.visit(value)
      if value is not node.values[-1]:
        self.attr(node, 'comma_%d' % i, [self.ws, ',', self.ws], default=', ')
    self.optional_token(node, 'extracomma', ',')
    self.attr(node, 'close_prefix', [self.ws, '}'], default='}')

  @expression
  def visit_DictComp(self, node):
    self.attr(node, 'open_dict', ['{', self.ws], default='{')
    self.visit(node.key)
    self.attr(node, 'key_val_sep', [self.ws, ':', self.ws], default=': ')
    self.visit(node.value)
    for i, comp in enumerate(node.generators):
      self.attr(node, 'for_%d' % i, [self.ws, 'for', self.ws], default=' for ')
      self.visit(comp)
    self.attr(node, 'close_dict', [self.ws, '}'], default='}')

  @expression
  def visit_GeneratorExp(self, node):
    self._comp_exp(node)

  @expression
  def visit_IfExp(self, node):
    self.visit(node.body)
    self.attr(node, 'if', [self.ws, 'if', self.ws], default=' if ')
    self.visit(node.test)
    self.attr(node, 'else', [self.ws, 'else', self.ws], default=' else ')
    self.visit(node.orelse)

  @expression
  def visit_Lambda(self, node):
    self.attr(node, 'lambda_def', ['lambda', self.ws], default='lambda ')
    self.visit(node.args)
    self.attr(node, 'open_lambda', [self.ws, ':', self.ws], default=': ')
    self.visit(node.body)

  @expression
  def visit_List(self, node):
    self.attr(node, 'list_open', ['[', self.ws], default='[')

    for i, elt in enumerate(node.elts):
      self.visit(elt)
      if elt is not node.elts[-1]:
        self.attr(node, 'comma_%d' % i, [self.ws, ',', self.ws], default=', ')
    if node.elts:
      self.optional_token(node, 'extracomma', ',')

    self.attr(node, 'list_close', [self.ws, ']'], default=']')

  @expression
  def visit_ListComp(self, node):
    self._comp_exp(node, open_brace='[', close_brace=']')

  def _comp_exp(self, node, open_brace=None, close_brace=None):
    if open_brace:
      self.attr(node, 'compexp_open', [open_brace, self.ws], default=open_brace)
    self.visit(node.elt)
    for i, comp in enumerate(node.generators):
      self.attr(node, 'for_%d' % i, [self.ws, 'for', self.ws], default=' for ')
      self.visit(comp)
    if close_brace:
      self.attr(node, 'compexp_close', [self.ws, close_brace],
                default=close_brace)

  @expression
  def visit_Name(self, node):
    self.token(node.id)

  @expression
  def visit_NameConstant(self, node):
    self.token(str(node.value))

  @expression
  def visit_Repr(self, node):
    self.attr(node, 'repr_open', ['repr', self.ws, '('], default='repr(')
    self.visit(node.value)
    self.attr(node, 'repr_close', [self.ws, ')'], default=')')

  @expression
  def visit_Set(self, node):
    self.attr(node, 'set_open', ['{', self.ws], default='{')

    for i, elt in enumerate(node.elts):
      self.visit(elt)
      if elt is not node.elts[-1]:
        self.attr(node, 'comma_%d' % i, [self.ws, ',', self.ws], default=', ')
    if node.elts:
      self.optional_token(node, 'extracomma', ',')

    self.attr(node, 'set_close', [self.ws, '}'], default='}')

  @expression
  def visit_SetComp(self, node):
    self._comp_exp(node, open_brace='{', close_brace='}')

  @expression
  def visit_Subscript(self, node):
    self.visit(node.value)
    self.visit(node.slice)

  @expression
  def visit_Tuple(self, node):
    for i, elt in enumerate(node.elts):
      self.visit(elt)
      if elt is not node.elts[-1]:
        self.attr(node, 'comma_%d' % i, [self.ws, ',', self.ws], default=', ')
    if node.elts:
      self.optional_token(node, 'extracomma', ',')

  @expression
  def visit_UnaryOp(self, node):
    self.visit(node.op)
    self.visit(node.operand)

  # ============================================================================
  # == OPERATORS AND TOKENS: Anything that's just whitespace and tokens       ==
  # ============================================================================

  @space_around
  def visit_Ellipsis(self, node):
    self.token('...')

  @space_around
  def visit_Add(self, node):
    self.token(ast_constants.NODE_TYPE_TO_TOKENS[type(node)][0])

  @space_around
  def visit_Sub(self, node):
    self.token(ast_constants.NODE_TYPE_TO_TOKENS[type(node)][0])

  @space_around
  def visit_Mult(self, node):
    self.token(ast_constants.NODE_TYPE_TO_TOKENS[type(node)][0])

  @space_around
  def visit_Div(self, node):
    self.token(ast_constants.NODE_TYPE_TO_TOKENS[type(node)][0])

  @space_around
  def visit_Mod(self, node):
    self.token(ast_constants.NODE_TYPE_TO_TOKENS[type(node)][0])

  @space_around
  def visit_Pow(self, node):
    self.token(ast_constants.NODE_TYPE_TO_TOKENS[type(node)][0])

  @space_around
  def visit_LShift(self, node):
    self.token(ast_constants.NODE_TYPE_TO_TOKENS[type(node)][0])

  @space_around
  def visit_RShift(self, node):
    self.token(ast_constants.NODE_TYPE_TO_TOKENS[type(node)][0])

  @space_around
  def visit_BitAnd(self, node):
    self.token(ast_constants.NODE_TYPE_TO_TOKENS[type(node)][0])

  @space_around
  def visit_BitOr(self, node):
    self.token(ast_constants.NODE_TYPE_TO_TOKENS[type(node)][0])

  @space_around
  def visit_BitXor(self, node):
    self.token(ast_constants.NODE_TYPE_TO_TOKENS[type(node)][0])

  @space_around
  def visit_FloorDiv(self, node):
    self.token(ast_constants.NODE_TYPE_TO_TOKENS[type(node)][0])

  @space_around
  def visit_Invert(self, node):
    self.token(ast_constants.NODE_TYPE_TO_TOKENS[type(node)][0])

  @space_around
  def visit_Not(self, node):
    self.token(ast_constants.NODE_TYPE_TO_TOKENS[type(node)][0])

  @space_around
  def visit_UAdd(self, node):
    self.token(ast_constants.NODE_TYPE_TO_TOKENS[type(node)][0])

  @space_around
  def visit_USub(self, node):
    self.token(ast_constants.NODE_TYPE_TO_TOKENS[type(node)][0])

  @space_around
  def visit_Eq(self, node):
    self.token(ast_constants.NODE_TYPE_TO_TOKENS[type(node)][0])

  @space_around
  def visit_NotEq(self, node):
    self.token(ast_constants.NODE_TYPE_TO_TOKENS[type(node)][0])

  @space_around
  def visit_Lt(self, node):
    self.token(ast_constants.NODE_TYPE_TO_TOKENS[type(node)][0])

  @space_around
  def visit_LtE(self, node):
    self.token(ast_constants.NODE_TYPE_TO_TOKENS[type(node)][0])

  @space_around
  def visit_Gt(self, node):
    self.token(ast_constants.NODE_TYPE_TO_TOKENS[type(node)][0])

  @space_around
  def visit_GtE(self, node):
    self.token(ast_constants.NODE_TYPE_TO_TOKENS[type(node)][0])

  @space_around
  def visit_Is(self, node):
    self.token(ast_constants.NODE_TYPE_TO_TOKENS[type(node)][0])

  @space_around
  def visit_IsNot(self, node):
    self.attr(node, 'content', ['is', self.ws, 'not'], default='is not')

  @space_around
  def visit_In(self, node):
    self.token(ast_constants.NODE_TYPE_TO_TOKENS[type(node)][0])

  @space_around
  def visit_NotIn(self, node):
    self.attr(node, 'content', ['not', self.ws, 'in'], default='not in')

  # ============================================================================
  # == MISC NODES: Nodes which are neither statements nor expressions         ==
  # ============================================================================

  @space_left
  def visit_alias(self, node):
    name_pattern = []
    parts = node.name.split('.')
    for part in parts[:-1]:
      name_pattern += [self.ws, part, self.ws, '.']
    name_pattern += [self.ws, parts[-1]]
    self.attr(node, 'name', name_pattern,
              deps=('name',),
              default=node.name)
    if node.asname is not None:
      self.attr(node, 'asname', [self.ws, 'as', self.ws], default=' as ')
      self.token(node.asname)

  @space_around
  def visit_arg(self, node):
    self.token(node.arg)
    if node.annotation is not None:
      self.attr(node, 'annotation_prefix', [self.ws, ':', self.ws],
                default=': ')
      self.visit(node.annotation)

  @space_around
  def visit_arguments(self, node):
    total_args = (len(node.args) +
                  (1 if node.vararg else 0) +
                  (1 if node.kwarg else 0))
    arg_i = 0

    positional = node.args[:-len(node.defaults)] if node.defaults else node.args
    keyword = node.args[-len(node.defaults):] if node.defaults else node.args

    for arg in positional:
      self.visit(arg)
      arg_i += 1
      if arg_i < total_args:
        self.attr(node, 'comma_%d' % arg_i, [self.ws, ',', self.ws],
                  default=', ')

    for i, arg, default in zip(range(len(keyword)), keyword, node.defaults):
      self.visit(arg)
      self.attr(node, 'default_%d' % i, [self.ws, '=', self.ws],
                default='=')
      self.visit(default)
      arg_i += 1
      if arg_i < total_args:
        self.attr(node, 'comma_%d' % arg_i, [self.ws, ',', self.ws],
                  default=', ')

    if node.vararg:
      self.attr(node, 'vararg_prefix', [self.ws, '*', self.ws], default='*')
      if isinstance(node.vararg, ast.AST):
        self.visit(node.vararg)
      else:
        self.token(node.vararg)
        self.attr(node, 'vararg_suffix', [self.ws])
      arg_i += 1
      if arg_i < total_args:
        self.token(',')

    if node.kwarg:
      self.attr(node, 'kwarg_prefix', [self.ws, '**', self.ws], default='**')
      if isinstance(node.kwarg, ast.AST):
        self.visit(node.kwarg)
      else:
        self.token(node.kwarg)
        self.attr(node, 'kwarg_suffix', [self.ws])

    if positional or keyword or node.vararg or node.kwarg:
      self.optional_token(node, 'extracomma', ',')

  @space_around
  def visit_comprehension(self, node):
    self.visit(node.target)
    self.attr(node, 'in', [self.ws, 'in', self.ws], default=' in ')
    self.visit(node.iter)
    for i, if_expr in enumerate(node.ifs):
      self.attr(node, 'if_%d' % i, [self.ws, 'if', self.ws], default=' if ')
      self.visit(if_expr)

  @space_around
  def visit_keyword(self, node):
    self.token(node.arg)
    self.attr(node, 'eq', [self.ws, '='], default='=')
    self.visit(node.value)

  @space_left
  def visit_Index(self, node, in_ext=False):
    if len(self._stack) > 1 and not isinstance(self._stack[-2], ast.ExtSlice):
      self.attr(node, 'index_open', ['[', self.ws], default='[')
    self.visit(node.value)
    if len(self._stack) > 1 and not isinstance(self._stack[-2], ast.ExtSlice):
      self.attr(node, 'index_close', [self.ws, ']'], default=']')

  @space_left
  def visit_ExtSlice(self, node):
    self.token('[')
    for i, dim in enumerate(node.dims):
      self.visit(dim)
      if dim is not node.dims[-1]:
        self.attr(node, 'dim_sep_%d' % i, [self.ws, ',', self.ws], default=', ')

    self.token(']')

  @space_left
  def visit_Slice(self, node):
    if len(self._stack) > 1 and not isinstance(self._stack[-2], ast.ExtSlice):
      self.attr(node, 'index_open', ['[', self.ws], default='[')

    if node.lower:
      self.visit(node.lower)

    self.attr(node, 'lowerspace', [self.ws, ':', self.ws])

    if node.upper:
      self.visit(node.upper)

    if node.step:
      self.attr(node, 'stepspace', [self.ws, ':', self.ws])
      self.visit(node.step)

    if len(self._stack) > 1 and not isinstance(self._stack[-2], ast.ExtSlice):
      self.attr(node, 'index_close', [self.ws, ']'], default=']')


class AnnotationError(Exception):
  """An exception for when we failed to annotate the tree."""


class AstAnnotator(BaseVisitor):

  def __init__(self, source):
    super(AstAnnotator, self).__init__()
    self.tokens = token_generator.TokenGenerator(source)
    self._indent = ''
    self._indent_diff = ''

  def visit(self, node):
    try:
      ast_utils.setprop(node, 'indent', self._indent)
      ast_utils.setprop(node, 'indent_diff', self._indent_diff)
      super(AstAnnotator, self).visit(node)
    except (TypeError, ValueError, IndexError, KeyError) as e:
      raise AnnotationError(e)

  def indented(self, node, children_attr):
    """Annotate children with their indentation level and iterate over them."""
    children = getattr(node, children_attr)
    cur_loc = self.tokens._loc
    next_loc = self.tokens.peek().start
    # Special case: if the first child is on the same line, then there is no
    # indentation level to track.
    if len(children) == 1 and cur_loc[0] == next_loc[0]:
      yield children[0]
      return

    prev_indent = self._indent
    prev_indent_diff = self._indent_diff

    # Find the indent level of the first child
    new_indent = ''.join(itertools.takewhile(
        lambda s: s in ' \t', self.tokens.lines[children[0].lineno - 1]))
    if (not new_indent.startswith(prev_indent) or
        len(new_indent) <= len(prev_indent)):
      raise AnnotationError('Indent detection failed; inner indentation level '
                            'is not more than the outer indentation.')

    # Set the indent level to the child's indent and iterate over the children
    self._indent = new_indent
    self._indent_diff = new_indent[len(prev_indent):]
    for child in children:
      yield child
    # Store the suffix at this indentation level, which could be many lines
    ast_utils.setprop(node, 'block_suffix_%s' % children_attr,
                      self.tokens.block_whitespace(self._indent))

    # Dedent back to the pervious level
    self._indent = prev_indent
    self._indent_diff = prev_indent_diff

  @expression
  def visit_Num(self, node):
    """Annotate a Num node with the exact number format."""
    token_number_type = token_generator.TOKENS.NUMBER
    contentargs = [lambda: self.tokens.next_of_type(token_number_type).src]
    if node.n < 0:
      contentargs.insert(0, '-')
    self.attr(node, 'content', contentargs, deps=('n',), default=str(node.n))

  @expression
  def visit_Str(self, node):
    """Annotate a Str node with the exact string format."""
    self.attr(node, 'content', [self.tokens.str], deps=('s',), default=node.s)

  @space_around
  def visit_Ellipsis(self, node):
    # Ellipsis is sometimes split into 3 tokens and other times a single token
    # Account for both forms when parsing the input.
    if self.tokens.peek().src == '...':
      self.token('...')
    else:
      for i in range(3):
        self.token('.')

  def check_is_elif(self, node):
    """Return True iff the If node is an `elif` in the source."""
    next_tok = self.tokens.next_name()
    return isinstance(node, ast.If) and next_tok.src == 'elif'

  def check_is_continued_with(self, node):
    """Return True iff the With node is a continued `with` in the source."""
    return isinstance(node, ast.With) and self.tokens.peek().src == ','

  def ws(self, max_lines=None):
    """Parse some whitespace from the source tokens and return it."""
    return self.tokens.whitespace(max_lines=max_lines)

  def token(self, token_val):
    """Parse a single token with exactly the given value."""
    token = self.tokens.next()
    if token.src != token_val:
      raise AnnotationError("Expected %r but found %r\nline %d: %s" % (
          token_val, token.src, token.start[0], token.line))

    # If the token opens or closes a parentheses scope, keep track of it
    if token.src in '({[':
      self.tokens.hint_open()
    elif token.src in ')}]':
      self.tokens.hint_closed()

    return token.src

  def optional_token(self, node, attr_name, token_val):
    """Try to parse a token and attach it to the node."""
    token = self.tokens.peek()
    if token and token.src == token_val:
      self.tokens.next()
      ast_utils.appendprop(node, attr_name, token.src + self.ws())

  def attr(self, node, attr_name, attr_vals, deps=None, default=None):
    """Parses some source and sets an attribute on the given node.

    Stores some arbitrary formatting information on the node. This takes a list
    attr_vals which tell what parts of the source to parse. The result of each
    function is concatenated onto the formatting data, and strings in this list
    are a shorthand to look for an exactly matching token.

    For example:
      self.attr(node, 'foo', ['(', self.ws, 'Hello, world!', self.ws, ')'],
                deps=('s',), default=node.s)

    is a rudimentary way to parse a parenthesized string. After running this,
    the matching source code for this node will be stored in its formatting
    dict under the key 'foo'. The result might be `(\n  'Hello, world!'\n)`.

    This also keeps track of the current value of each of the dependencies.
    In the above example, we would have looked for the string 'Hello, world!'
    because that's the value of node.s, however, when we print this back, we
    want to know if the value of node.s has changed since this time. If any of
    the dependent values has changed, the default would be used instead.

    Arguments:
      node: (ast.AST) An AST node to attach formatting information to.
      attr_name: (string) Name to store the formatting information under.
      attr_vals: (list of functions/strings) Each item is either a function
        that parses some source and return a string OR a string to match
        exactly (as a token).
      deps: (optional, set of strings) Attributes of the node which attr_vals
        depends on.
      default: (string) Unused here.
    """
    del default  # unused
    if deps:
      for dep in deps:
        ast_utils.setprop(node, dep + '__src', getattr(node, dep, None))
    attr_parts = []
    for attr_val in attr_vals:
      if isinstance(attr_val, six.string_types):
        attr_parts.append(self.token(attr_val))
      else:
        attr_parts.append(attr_val())
    ast_utils.setprop(node, attr_name, ''.join(attr_parts))

  def scope(self, node):
    """Return a context manager to handle a parenthesized scope."""
    return self.tokens.scope(node)

  def _optional_token(self, token_type, token_val):
    token = self.tokens.peek()
    if not token or token.type != token_type or token.src != token_val:
      return ''
    else:
      self.tokens.next()
      return token.src + self.ws()
