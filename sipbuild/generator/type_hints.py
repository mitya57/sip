# Copyright (c) 2022, Riverbank Computing Limited
# All rights reserved.
#
# This copy of SIP is licensed for use under the terms of the SIP License
# Agreement.  See the file LICENSE for more details.
#
# This copy of SIP may also used under the terms of the GNU General Public
# License v2 or v3 as published by the Free Software Foundation which can be
# found in the files LICENSE-GPL2 and LICENSE-GPL3 included in this package.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.


from copy import copy
from dataclasses import dataclass, field
from enum import auto, Enum
from typing import List, Optional, Union
from weakref import WeakKeyDictionary

from ..exceptions import UserException

from .formatters import EnumFormatter, ClassFormatter
from .scoped_name import ScopedName
from .specification import TypeHint, WrappedClass, WrappedEnum


# The types defined in the typing module.
_TYPING_MODULE = (
    'Any', 'NoReturn', 'Tuple', 'Union', 'Optional', 'Callable', 'Type',
    'Literal', 'ClassVar', 'Final', 'Annotated', 'AnyStr', 'Protocol',
    'NamedTuple', 'Dict', 'List', 'Set', 'FrozenSet', 'IO', 'TextIO',
    'BinaryIO', 'Pattern', 'Match', 'Text', 'Iterable', 'Iterator',
    'Generator', 'Mapping', 'Sequence',
)


class NodeType(Enum):
    """ The node types. """

    TYPING = auto()
    CLASS = auto()
    ENUM = auto()
    OTHER = auto()


class ParseState(Enum):
    """ The different parse states of a type hint. """

    REQUIRED = auto()
    PARSING = auto()
    PARSED = auto()


@dataclass
class ManagedTypeHint:
    """ Encapsulate a managed type hint. """

    # The type hint being managed.
    type_hint: TypeHint

    # The parse state.
    parse_state: ParseState = ParseState.REQUIRED

    # The rendered reST reference.
    rest_ref: Optional[str] = None

    # The root node.
    root: Optional['TypeHintNode'] = None


@dataclass
class TypeHintNode:
    """ Encapsulate a node of a parsed type hint. """

    # The type.
    type: NodeType

    # The list of child nodes.
    children: Optional[List['TypeHintNode']] = None

    # The type-dependent definition.
    definition: Optional[Union[str, WrappedClass, WrappedEnum]] = None

    # The next sibling node.
    next: Optional['TypeHintNode'] = None


class TypeHintManager:
    """ A manager for type hints on behalf of a Specification object. """

    # The map of specification objects and the corresponding manager object.
    _spec_manager_map = WeakKeyDictionary()

    def __new__(cls, spec):
        """ Return the existing manager for a specification or create a new one
        if necessary.
        """

        try:
            manager = cls._spec_manager_map[spec]
        except KeyError:
            manager = object.__new__(cls)

            manager._spec = spec
            manager._managed_type_hints = {}

            cls._spec_manager_map[spec] = manager

        return manager

    def get_type_hint(self, text):
        """ Return a unique (to the specification) TypeHint object for the text
        of a hint.
        """

        try:
            managed_type_hint = self._managed_type_hints[text]
        except KeyError:
            managed_type_hint = ManagedTypeHint(TypeHint(text))
            self._managed_type_hints[text] = managed_type_hint

        return managed_type_hint.type_hint

    def rest_ref(self, type_hint, out):
        """ Return the type hint with appropriate reST references. """

        managed_type_hint = self._managed_type_hints[type_hint.text]

        # See if it needs rendering.
        if managed_type_hint.rest_ref is None:
            managed_type_hint.rest_ref = self._render(managed_type_hint, out,
                    rest_ref=True)

        return managed_type_hint.rest_ref

    def _parse(self, managed_type_hint, out):
        """ Ensure a type hint has been parsed. """

        if managed_type_hint.parse_state is ParseState.REQUIRED:
            managed_type_hint.parse_state = ParseState.PARSING
            managed_type_hint.root = self._parse_node(out,
                    managed_type_hint.type_hint.text)
            managed_type_hint.parse_state = ParseState.PARSED

    def _parse_node(self, out, text, start=0, end=None):
        """ Return a single node of a parsed type hint. """

        if end is None:
            end = len(text)
            top_level = True
        else:
            top_level = False

        # Find the name and any opening and closing brackets.
        start = self._strip_leading(text, start, end)
        name_start = start

        end = self._strip_trailing(text, start, end)
        name_end = end

        children = None

        i = text[start:end].find('[')
        if i >= 0:
            i += start

            children = []

            # The last character must be a closing bracket.
            if text[end - 1] != ']':
                raise UserException(
                        f"type hint '{text}': ']' expected at position {end}")

            # Find the end of any name.
            name_end = self._strip_trailing(text, name_start, i)

            while True:
                # Skip the opening bracket or comma.
                i += 1

                # Find the next comma, if any.
                depth = 0

                for part_i in range(i, end):
                    if text[part_i] == '[':
                        depth += 1

                    elif text[part_i] == ']' and depth != 0:
                        depth -= 1

                    elif text[part_i] in ',]' and depth == 0:
                        # Recursively parse this part.
                        new_child = self._parse_node(out, text, i, part_i)
                        if new_child is not None:
                            self._append_child(children, new_child)

                        i = part_i
                        break
                else:
                    break

        # See if we have a name.
        if name_start != name_end:
            # Get the name. */
            name = text[name_start:name_end]

            # See if it is an object in the typing module.
            if name in _TYPING_MODULE:
                if name == 'Union':
                    # If there are no children assume it is because they have
                    # been omitted.
                    if len(children) == 0:
                        return None

                    # Flatten any unions.
                    flattened = []

                    for child in children:
                        if child.type is NodeType.TYPING and child.definition == 'Union':
                            for grandchild in child.children:
                                self._append_child(flattened, grandchild)
                        else:
                            self._append_child(flattened, child)

                    children = flattened

                node = TypeHintNode(NodeType.TYPING, children=children,
                        definition=name)
            else:
                # Only objects from the typing module can have children.
                if children is not None:
                    raise UserException(
                            f"type hint '{text}': brackets are invalid")

                # Search for the type.
                node = self._lookup_type(name, out)
        else:
            # At the top level we must have brackets and they must not be empty.
            if top_level and (children is None or len(children) == 0):
                raise UserException(
                        f"type hint '{text}': must have non-empty brackets")

            # Return the representation of brackets.
            node = TypeHintNode(NodeType.TYPING, children=children)

        return node

    def _render(self, managed_type_hint, out, pep484=False, rest_ref=False,
            module=None, defined=None):
        """ Return a rendered type hint. """

        self._parse(managed_type_hint, out)

        if managed_type_hint.root is not None:
            s = self._render_node(managed_type_hint.root, out, pep484,
                    rest_ref, module, defined)
        else:
            s = self._maybe_any_object(managed_type_hint.type_hint.text,
                    pep484=pep484)

        return s

    def _render_node(self, node, out, pep484, rest_ref, module, defined):
        """ Render a single node. """

        if node.type is NodeType.TYPING:
            if node.definition is None:
                s = ''
            elif pep484:
                s = 'typing.' + node.definition
            else:
                s = node.definition

            if node.children is not None:
                children = [self._render_node(c, out, pep484, rest_ref, module,
                        defined) for c in node.children]

                s += '[' + ', '.join(children) + ']'

        elif node.type is NodeType.CLASS:
            formatter = ClassFormatter(self._spec, node.definition)

            if rest_ref:
                s = formatter.rest_ref
            elif pep484:
                s = formatter.type_hint(module, defined)
            else:
                s = formatter.fq_py_name

        elif node.type is NodeType.ENUM:
            formatter = EnumFormatter(self._spec, node.definition)

            if rest_ref:
                s = formatter.rest_ref
            elif pep484:
                s = formatter.type_hint(module, defined)
            else:
                s = formatter.fq_py_name

        else:
            s = self._maybe_any_object(node.definition, pep484)

        return s

    @staticmethod
    def _append_child(children, new_child):
        """ Append a child to an existing list of children. """

        if len(children) > 1:
            children[-1].next = new_child

        children.append(new_child)

    def _copy_type_hint(self, type_hint, out):
        """ Copy the root node of a type hint. """

        managed_type_hint = self._managed_type_hints[type_hint.text]

        self._parse(managed_type_hint, out)

        if managed_type_hint.root is None:
            return None

        node = copy(managed_type_hint.root)
        node.next = None

        return node

    def _lookup_enum(self, name, scopes):
        """ Lookup an enum using its C/C++ name. """

        for enum in self._spec.enums:
            if enum.fq_cpp_name is not None and enum.fq_cpp_name.base_name == name and enum.scope in scopes:
                return enum

        return None

    def _lookup_class(self, name, scope):
        """ Lookup a class/struct/union using its C/C++ name. """

        for klass in self._spec.classes:
            if klass.scope is scope and not klass.external and klass.iface_file.fq_cpp_name.base_name == name:
                return klass

        return None

    def _lookup_mapped_type(self, name):
        """ Lookup a mapped type using its C/C++ name. """

        for mapped_type in self._spec.mapped_types:
            if mapped_type.cpp_name is not None and mapped_type.cpp_name.name == name:
                return mapped_type

        return None

    def _lookup_type(self, name, out):
        """ Look up a qualified Python type and return the corresponding node.
        """

        # Start searching at the global level.
        scope_klass = None
        scope_mapped_type = None

        # We allow both Python and C++ scope separators.
        scoped_name = ScopedName.parse(name.replace('.', '::'))

        for part_i, part in enumerate(scoped_name):
            is_last_part = ((part_i + 1) == len(scoped_name))

            # See if it's an enum.
            enum = self._lookup_enum(part, (scope_klass, scope_mapped_type))
            if enum is not None:
                if is_last_part:
                    return TypeHintNode(NodeType.ENUM, definition=enum)

                # There is some left so the whole lookup has failed.
                break

            # If we have a mapped type scope then we must be looking for an
            # enum, which we have failed to find.
            if scope_mapped_type is not None:
                break

            if scope_klass is None:
                # We are looking at the global level, so see if it is a mapped
                # type.
                mapped_type = self._lookup_mapped_type(part)
                if mapped_type is not None:
                    # If we have used the whole name then the lookup has
                    # succeeded.
                    if is_last_part:
                        if mapped_type.type_hints is not None:
                            type_hint = mapped_type.type_hints.hint_out if out else mapped_type.type_hints.hint_in

                            if type_hint is not None:
                                if self._managed_type_hints[type_hint.text].parse_state is not ParseState.PARSING:
                                    return self._copy_type_hint(type_hint, out)

                        return None

                    # Otherwise this is the scope for the next part.
                    scope_mapped_type = mapped_type

            if scope_mapped_type is None:
                # If we get here then it must be a class.
                klass = self._lookup_class(part, scope_klass)
                if klass is None:
                    break

                # If we have used the whole name then the lookup has succeeded.
                if is_last_part:
                    if klass.type_hints is not None:
                        type_hint = klass.type_hints.hint_out if out else klass.type_hints.hint_in

                        if type_hint is not None:
                            if self._managed_type_hints[type_hint.text].parse_state is not ParseState.PARSING:
                                return self._copy_type_hint(type_hint, out)

                    return TypeHintNode(NodeType.CLASS, definition=klass)

                # Otherwise this is the scope for the next part.
                scope_klass = klass

            # If we have run out of name then the lookup has failed.
            if is_last_part:
                break

        # Nothing was found.
        return TypeHintNode(NodeType.OTHER, definition=name)

    @classmethod
    def _maybe_any_object(cls, hint, pep484):
        """ Return a hint taking into account that it may be any sort of
        object.
        """

        return hint if hint != 'Any' else cls._any_object(pep484)

    @staticmethod
    def _any_object(pep484):
        """ Return a hint taking into account that it may be any sort of
        object.
        """

        return 'typing.Any' if pep484 else 'object'

    @staticmethod
    def _strip_leading(text, start, end):
        """ Return the index of the first non-space of a string. """
    
        while start < end and text[start] == ' ':
            start += 1

        return start

    @staticmethod
    def _strip_trailing(text, start, end):
        """ Return the index after the last non-space of a string. """

        while end > start and text[end - 1] == ' ':
            end -= 1

        return end
