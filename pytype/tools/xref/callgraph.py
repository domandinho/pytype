"""Trace function arguments, return values and calls to other functions."""

import collections

import attr
from pytype.pytd import pytd
from pytype.pytd import pytd_utils


@attr.s
class Attr(object):
  name = attr.ib()
  node_type = attr.ib()
  type = attr.ib()
  attrib = attr.ib()
  location = attr.ib()


@attr.s
class Arg(object):
  name = attr.ib()
  node_type = attr.ib()
  type = attr.ib()


@attr.s
class Param(object):
  name = attr.ib()
  type = attr.ib()


@attr.s
class Call(object):
  function_id = attr.ib()
  args = attr.ib()
  location = attr.ib()


@attr.s
class Function(object):
  id = attr.ib()
  params = attr.ib(factory=list)
  param_attrs = attr.ib(factory=list)
  local_attrs = attr.ib(factory=list)
  calls = attr.ib(factory=list)
  ret = attr.ib(default=None)
  location = attr.ib(default=None)


def unknown_to_any(typename):
  if '~unknown' in typename:
    return 'typing.Any'
  return typename


def unwrap_type(typ):
  if isinstance(typ, (pytd.ClassType, pytd.NamedType)):
    typ_name = typ.name
  elif isinstance(typ, pytd.UnionType):
    typ_name = 'Union[' + ', '.join(unwrap_type(t) for t in typ.type_list) + ']'
  elif isinstance(typ, pytd.AnythingType):
    typ_name = 'typing.Any'
  else:
    typ_name = pytd_utils.Print(typ)
  return unknown_to_any(typ_name)


def get_function_params(pytd_fn):
  """Collect function param types from pytype."""
  # We have turned call records on in the indexer, so a function will have a
  # pytd signature for every tuple of call args. Here we iterate through those
  # signatures and set every param's type to the union of every non-"unknown"
  # call type for that param.
  params = collections.OrderedDict()
  for sig in pytd_fn.signatures:
    for p in sig.params:
      if p.name not in params:
        params[p.name] = []
      if '~unknown' not in str(p.type):
        params[p.name].append(p.type)
  for k in params:
    params[k] = pytd_utils.JoinTypes(params[k])
  return [(k, unwrap_type(v)) for k, v in params.items()]


class FunctionMap(object):
  """Collect a map of function types and outbound callgraph edges."""

  def __init__(self, index):
    self.index = index
    self.fmap = self.init_from_index(index)

  def pytd_of_fn(self, f):
    d = f.data[0][0]
    return self.index.get_pytd_def(d, f.name)

  def init_from_index(self, index):
    """Initialize the function map."""
    out = {}
    fn_defs = [(k, v) for k, v in index.defs.items() if v.typ == 'FunctionDef']
    for fn_id, fn in fn_defs:
      params = get_function_params(self.pytd_of_fn(fn))
      params = [Param(name, typ) for name, typ in params]
      ret = index.envs[fn_id].ret
      if fn_id in index.locs:
        location = index.locs[fn_id][-1].location
      else:
        location = None
      out[fn_id] = Function(
          id=fn_id, params=params, ret=ret, location=location)
    # Add a slot for "module" to record function calls made at top-level
    out['module'] = Function(id='module')
    return out

  def add_attr(self, ref, defn):
    """Add an attr access within a function body."""
    attrib = ref.name
    scope = ref.ref_scope
    try:
      d = self.index.envs[scope].env[ref.target]
    except KeyError:
      return

    typename = unknown_to_any(defn.typename)
    attr_access = Attr(
        name=d.name,
        node_type=d.typ,
        type=typename,
        attrib=attrib,
        location=ref.location)
    fn = self.fmap[scope]
    if attr_access.node_type == 'Param':
      fn.param_attrs.append(attr_access)
    else:
      fn.local_attrs.append(attr_access)

  def add_param_def(self, ref, defn):
    """Add a function parameter definition."""
    fn = self.fmap[ref.ref_scope]
    for param in fn.params:
      # Don't override a type inferred from call sites.
      if param.name == defn.name and param.type in ('nothing', 'typing.Any'):
        param.type = unwrap_type(self.index.get_pytd(ref.data[0]))
        break

  def add_link(self, ref, defn):
    if ref.typ == 'Attribute':
      self.add_attr(ref, defn)
    if defn.typ == 'Param':
      self.add_param_def(ref, defn)

  def add_call(self, call):
    """Add a function call."""
    scope = call.scope
    env = self.index.envs[scope]
    args = []
    for name in call.args:
      if name in env.env:
        defn = env.env[name]
        node_type = defn.typ
        typename = unknown_to_any(defn.typename)
      else:
        node_type = None
        typename = 'typing.Any'
      args.append(Arg(name, node_type, typename))
    self.fmap[scope].calls.append(
        Call(call.func, args, call.location))


def collect_function_map(index):
  """Track types and outgoing calls within a function."""

  fns = FunctionMap(index)

  # Collect methods and attribute accesses
  for ref, defn in index.links:
    fns.add_link(ref, defn)

  # Collect function calls
  for call in index.calls:
    fns.add_call(call)

  return fns.fmap
