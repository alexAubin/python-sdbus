# SPDX-License-Identifier: LGPL-2.1-or-later

# Copyright (C) 2020  igo95862

# This file is part of py_sd_bus

# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2.1 of the License, or (at your option) any later version.

# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.

# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301 USA
from __future__ import annotations

from asyncio import Queue
from copy import deepcopy
from inspect import getfullargspec, getmembers
from types import FunctionType
from typing import (Any, AsyncGenerator, Callable, Coroutine, Dict, Generator,
                    Generic, Iterator, List, Optional, Sequence, Set, Tuple,
                    Type, TypeVar, cast)
from weakref import ref as weak_ref

from .sd_bus_internals import SdBus, SdBusInterface, SdBusMessage, sd_bus_open

DEFAULT_BUS: Optional[SdBus] = None


def get_bus() -> SdBus:
    global DEFAULT_BUS
    old_bus = DEFAULT_BUS
    if old_bus is None:
        new_bus = sd_bus_open()
        DEFAULT_BUS = new_bus
        return new_bus
    else:
        return old_bus


def _method_name_converter(python_name: str) -> Iterator[str]:
    char_iter = iter(python_name)
    # Name starting with upper case letter
    first_char = next(char_iter)
    yield first_char.upper()

    upper_next_one = False
    for c in char_iter:
        # Every under score remove and uppercase next one
        if c != '_':
            if upper_next_one:
                yield c.upper()
                upper_next_one = False
            else:
                yield c
        else:
            upper_next_one = True


T_input = TypeVar('T_input')
T_result = TypeVar('T_result')
T_obj = TypeVar('T_obj')


class DbusSomething:
    def __init__(self) -> None:
        self.interface_name: Optional[str] = None
        self.serving_enabled: bool = True


class DbusBinded:
    ...


class DbusMethod(DbusSomething):

    def __init__(
            self,
            async_function: FunctionType,
            method_name: Optional[str],
            input_signature: str,
            result_signature: str,
            result_args_names: Sequence[str],
            flags: int):

        super().__init__()
        self.original_method = async_function
        self.args_spec = getfullargspec(async_function)
        self.args_names = self.args_spec.args[1:]  # 1: because of self
        self.num_of_args = len(self.args_names)
        self.args_defaults = (
            self.args_spec.defaults
            if self.args_spec.defaults is not None
            else ())
        self.default_args_start_at = self.num_of_args - len(self.args_defaults)
        self.dbus_method: Optional[DbusMethodType] = None

        if method_name is None:
            self.method_name = ''.join(
                _method_name_converter(async_function.__name__))
        else:
            self.method_name = method_name

        self.input_signature = input_signature
        self.input_args_names = ""
        self.result_signature = result_signature
        self.result_args_names = result_args_names
        self.flags = flags

    def __get__(self,
                obj: DbusInterfaceBase,
                obj_class: Optional[Type[DbusInterfaceBase]] = None,
                ) -> Callable[..., Any]:
        return DbusMethodBinded(self, obj)


class DbusMethodBinded(DbusBinded):
    def __init__(self, dbus_method: DbusMethod, interface: DbusInterfaceBase):
        self.dbus_method = dbus_method
        self.interface = interface

    async def _call_dbus(self, *args: Any) -> Any:
        assert self.interface.attached_bus is not None
        assert self.interface.remote_service_name is not None
        assert self.interface.remote_object_path is not None
        assert self.dbus_method.interface_name is not None
        new_call_message = self.interface.attached_bus.new_method_call_message(
            self.interface.remote_service_name,
            self.interface.remote_object_path,
            self.dbus_method.interface_name,
            self.dbus_method.method_name,
        )
        if args:
            new_call_message.append_data(
                self.dbus_method.input_signature, *args)

        reply_message = await self.interface.attached_bus.call_async(
            new_call_message)
        return reply_message.get_contents()

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if self.interface.is_binded:
            if len(args) == self.dbus_method.num_of_args:
                assert not kwargs
                return self._call_dbus(*args)

            rebuilt_args = self._rebuild_args(
                self.dbus_method.original_method,
                *args,
                **kwargs)

            return self._call_dbus(*rebuilt_args)
        else:
            return self.dbus_method.original_method(
                self.interface, *args, **kwargs)

    def _rebuild_args(
            self,
            function: FunctionType,
            *args: Any,
            **kwargs: Dict[str, Any]) -> List[Any]:
        # 3 types of arguments
        # *args - should be passed directly
        # **kwargs - should be put in a proper order
        # defaults - should be retrived and put in proper order

        # Strategy:
        # Iterate over arg names
        # Use:
        # 1. Arg
        # 2. Kwarg
        # 3. Default

        # a, b, c, d, e
        #       ^ defaults start here
        # 5 - 3 = [2]
        # ^ total args
        #     ^ number of default args
        # First arg that supports default is
        # (total args - number of default args)
        passed_args_iter = iter(args)
        default_args_iter = iter(self.dbus_method.args_defaults)

        new_args_list: List[Any] = []

        for i, a_name in enumerate(self.dbus_method.args_spec.args[1:]):
            try:
                next_arg = next(passed_args_iter)
            except StopIteration:
                next_arg = None

            if i >= self.dbus_method.default_args_start_at:
                next_default_arg = next(default_args_iter)
            else:
                next_default_arg = None

            next_kwarg = kwargs.get(a_name)

            if next_arg is not None:
                new_args_list.append(next_arg)
            elif next_kwarg is not None:
                new_args_list.append(next_kwarg)
            elif next_default_arg is not None:
                new_args_list.append(next_default_arg)
            else:
                raise TypeError('Could not flatten the args')

        return new_args_list

    async def _call_from_dbus(
            self,
            request_message: SdBusMessage) -> None:
        request_data = request_message.get_contents()

        reply_message = request_message.create_reply()

        local_method = self.dbus_method.original_method.__get__(
            self.interface, None)

        if isinstance(request_data, tuple):
            reply_data = await local_method(*request_data)
        elif request_data is None:
            reply_data = await local_method()
        else:
            reply_data = await local_method(request_data)

        if isinstance(reply_data, tuple):
            reply_message.append_data(
                self.dbus_method.result_signature, *reply_data)
        elif reply_data is not None:
            reply_message.append_data(
                self.dbus_method.result_signature, reply_data)

        reply_message.send()


def dbus_method(
        method_name: Optional[str] = None,
        input_signature: str = "",
        result_signature: str = "",
        result_args_names: Sequence[str] = (),
        flags: int = 0) -> Callable[[T_input], T_input]:

    def dbus_method_decorator(async_function: T_input) -> T_input:
        assert isinstance(async_function, FunctionType)
        new_wrapper = DbusMethod(
            async_function=async_function,
            method_name=method_name,
            input_signature=input_signature,
            result_signature=result_signature,
            result_args_names=result_args_names,
            flags=flags,
        )

        return cast(T_input, new_wrapper)

    return dbus_method_decorator


T = TypeVar('T')


class DbusProperty(DbusSomething, Generic[T]):
    def __init__(
            self,
            property_name: str,
            property_signature: str,
            property_get: Callable[[DbusInterfaceBase], T],
            property_set: Optional[Callable[[DbusInterfaceBase, T], None]],
            flags: int,

    ) -> None:
        super().__init__()
        self.property_name = property_name
        self.property_signature = property_signature
        self.property_get = property_get
        self.property_set = property_set
        self.flags = flags

    def __get__(self,
                obj: DbusInterfaceBase,
                obj_class: Optional[Type[DbusInterfaceBase]] = None,
                ) -> DbusPropertyBinded:
        return DbusPropertyBinded(self, obj)

    def setter(self,
               new_set_function: Callable[[Any, T], None]
               ) -> None:
        self.property_set = new_set_function


class DbusPropertyBinded(DbusBinded):
    def __init__(self,
                 dbus_property: DbusProperty[T],
                 interface: DbusInterfaceBase):
        self.dbus_property = dbus_property
        self.interface = interface

    def __await__(self) -> Generator[Any, None, T]:
        return self.get_async().__await__()

    def get_sync(self) -> T:
        return self.dbus_property.property_get(self.interface)

    def set_sync(self, complete_object: T) -> None:
        if self.dbus_property.property_set is None:
            raise ValueError('Property has no setter')

        self.dbus_property.property_set(self.interface, complete_object)
        return

    async def get_async(self) -> T:
        if not self.interface.is_binded:
            return self.dbus_property.property_get(self.interface)

        assert self.interface.attached_bus is not None
        assert self.interface.remote_service_name is not None
        assert self.interface.remote_object_path is not None
        assert self.dbus_property.property_name is not None
        assert self.dbus_property.interface_name is not None
        new_call_message = self.interface.attached_bus. \
            new_property_get_message(
                self.interface.remote_service_name,
                self.interface.remote_object_path,
                self.dbus_property.interface_name,
                self.dbus_property.property_name,
            )

        reply_message = await self.interface.attached_bus. \
            call_async(new_call_message)
        return cast(T, reply_message.get_contents()[1])

    async def set_async(self, complete_object: T) -> None:
        if not self.interface.is_binded:
            if self.dbus_property.property_set is None:
                raise ValueError('Property has no setter')

            self.dbus_property.property_set(self.interface, complete_object)
            return

        assert self.interface.attached_bus is not None
        assert self.interface.remote_service_name is not None
        assert self.interface.remote_object_path is not None
        assert self.dbus_property.property_name is not None
        assert self.dbus_property.interface_name is not None
        new_call_message = self.interface.attached_bus. \
            new_property_set_message(
                self.interface.remote_service_name,
                self.interface.remote_object_path,
                self.dbus_property.interface_name,
                self.dbus_property.property_name,
            )

        new_call_message.append_data(
            'v', (self.dbus_property.property_signature, complete_object))

        await self.interface.attached_bus.call_async(new_call_message)


def dbus_property(
        property_signature: str = "",
        property_name: Optional[str] = None,
        flags: int = 0,
) -> Callable[
    [Callable[[Any], T]],
        DbusProperty[T]]:

    def property_decorator(
        function: Callable[..., Any]
    ) -> DbusProperty[T]:
        nonlocal property_name

        if property_name is None:
            property_name = ''.join(
                _method_name_converter(
                    cast(FunctionType, function).__name__
                )
            )

        new_wrapper: DbusProperty[T] = DbusProperty(
            property_name,
            property_signature,
            function,
            None,
            flags,
        )

        return new_wrapper

    return property_decorator


class DbusSignal(Generic[T], DbusSomething):
    def __init__(
        self,
        signal_name: str,
        signature: str = "",
        args_names: Sequence[str] = (),
        flags: int = 0,
    ):
        super().__init__()
        self.signal_name = signal_name
        self.signature = signature
        self.args_names = args_names
        self.flags = flags

    def __get__(self,
                obj: DbusInterfaceBase,
                obj_class: Optional[Type[DbusInterfaceBase]] = None,
                ) -> DbusSignalBinded[T]:
        return DbusSignalBinded(self, obj)


class DbusSignalBinded(Generic[T], DbusBinded):
    def __init__(self,
                 dbus_signal: DbusSignal[T],
                 interface: DbusInterfaceBase):
        self.dbus_signal = dbus_signal
        self.interface = interface

    async def _get_dbus_queue(self) -> Queue[SdBusMessage]:
        assert self.interface.attached_bus is not None
        assert self.interface.remote_service_name is not None
        assert self.interface.remote_object_path is not None
        assert self.dbus_signal.interface_name is not None
        assert self.dbus_signal.signal_name is not None

        return await self.interface.attached_bus.get_signal_queue_async(
            self.interface.remote_service_name,
            self.interface.remote_object_path,
            self.dbus_signal.interface_name,
            self.dbus_signal.signal_name,
        )

    def _cleanup_local_queue(
            self,
            queue_ref: weak_ref[Queue[T]]) -> None:
        self.interface._local_signal_queues[self.dbus_signal].remove(queue_ref)

    def _get_local_queue(self) -> Queue[T]:
        try:
            list_of_queues = self.interface._local_signal_queues[
                self.dbus_signal]
        except KeyError:
            list_of_queues = []
            self.interface._local_signal_queues[
                self.dbus_signal] = list_of_queues

        new_queue: Queue[T] = Queue()

        list_of_queues.append(weak_ref(new_queue, self._cleanup_local_queue))

        return new_queue

    async def __aiter__(self) -> AsyncGenerator[T, None]:

        if self.interface.is_binded:
            message_queue = await self._get_dbus_queue()

            while True:
                next_signal_message = await message_queue.get()
                yield cast(T, next_signal_message.get_contents())
        else:
            data_queue = self._get_local_queue()

            while True:
                next_data = await data_queue.get()
                yield next_data

    def _emit_message(self, args: T) -> None:
        assert self.interface.attached_bus is not None
        assert self.interface.serving_object_path is not None
        assert self.dbus_signal.interface_name is not None
        assert self.dbus_signal.signal_name is not None

        signal_message = self.interface.attached_bus.new_signal_message(
            self.interface.serving_object_path,
            self.dbus_signal.interface_name,
            self.dbus_signal.signal_name,
        )

        if ((not self.dbus_signal.signature.startswith('('))
            and
                isinstance(args, tuple)):
            signal_message.append_data(self.dbus_signal.signature, *args)
        else:
            signal_message.append_data(self.dbus_signal.signature, args)

        signal_message.send()

    def emit(self, args: T) -> None:
        if self.interface.activated_interfaces:
            self._emit_message(args)

        try:
            list_of_queues = self.interface._local_signal_queues[
                self.dbus_signal]
        except KeyError:
            return

        for local_queue_ref in list_of_queues:
            local_queue = local_queue_ref()
            assert local_queue is not None
            local_queue.put_nowait(args)


class DbusOverload:
    def __init__(self, original: T):
        self.original = original


def dbus_overload(new_function: T) -> T:
    return cast(T, DbusOverload(new_function))


class DbusInterfaceMeta(type):
    def __new__(cls, name: str,
                bases: Tuple[type, ...],
                namespace: Dict[str, Any],
                interface_name: Optional[str] = None,
                serving_enabled: bool = True,
                ) -> DbusInterfaceMeta:

        declared_interfaces = set()
        # Set interface name
        for key, value in namespace.items():
            if isinstance(value, DbusSomething):
                value.interface_name = interface_name
                value.serving_enabled = serving_enabled
                declared_interfaces.add(key)

        super_declared_interfaces = set()
        for base in bases:
            if issubclass(base, DbusInterfaceBase):
                super_declared_interfaces.update(
                    base._dbus_declared_interfaces)

        for key in super_declared_interfaces & namespace.keys():
            if isinstance(namespace[key], DbusOverload):
                for base in bases:
                    try:
                        sc_dbus_def = base.__dict__[key]
                        break
                    except KeyError:
                        continue

                assert isinstance(sc_dbus_def, DbusSomething)
                new_dbus_def = deepcopy(sc_dbus_def)
                if isinstance(new_dbus_def, DbusMethod):
                    new_dbus_def.original_method = namespace[key].original
                else:
                    raise TypeError

                namespace[key] = new_dbus_def
                declared_interfaces.add(key)
            else:
                raise TypeError("Attempted to overload dbus defenition"
                                " without using @dbus_overload decorator")

        namespace['_dbus_declared_interfaces'] = declared_interfaces

        namespace['_dbus_interface_name'] = interface_name
        namespace['_dbus_serving_enabled'] = serving_enabled
        new_cls = super().__new__(cls, name, bases, namespace)

        return cast(DbusInterfaceMeta, new_cls)


class DbusInterfaceBase(metaclass=DbusInterfaceMeta):
    _dbus_declared_interfaces: Set[str]
    _dbus_interface_name: Optional[str]
    _dbus_serving_enabled: bool

    def __init__(self) -> None:
        self.activated_interfaces: List[SdBusInterface] = []
        self.is_binded: bool = False
        self.remote_service_name: Optional[str] = None
        self.remote_object_path: Optional[str] = None
        self.attached_bus: Optional[SdBus] = None
        self.serving_object_path: Optional[str] = None
        self._local_signal_queues: \
            Dict[DbusSignal[Any], List[weak_ref[Queue[Any]]]] = {}

    async def start_serving(self, bus: SdBus, object_path: str) -> None:
        # TODO: Being able to serve multiple busses and object
        self.attached_bus = bus
        self.serving_object_path = object_path
        # TODO: can be optimized with a single loop
        interface_map: Dict[str, List[DbusBinded]] = {}

        for key, value in getmembers(self):
            assert not isinstance(value, DbusSomething)

            if isinstance(value, DbusMethodBinded):
                interface_name = value.dbus_method.interface_name
                if not value.dbus_method.serving_enabled:
                    continue
            elif isinstance(value, DbusPropertyBinded):
                interface_name = value.dbus_property.interface_name
                if not value.dbus_property.serving_enabled:
                    continue
            else:
                continue

            assert interface_name is not None

            try:
                interface_member_list = interface_map[interface_name]
            except KeyError:
                interface_member_list = []
                interface_map[interface_name] = interface_member_list

            interface_member_list.append(value)

        for interface_name, member_list in interface_map.items():
            new_interface = SdBusInterface()
            for dbus_something in member_list:
                if isinstance(dbus_something, DbusMethodBinded):
                    new_interface.add_method(
                        dbus_something.dbus_method.method_name,
                        dbus_something.dbus_method.input_signature,
                        dbus_something.dbus_method.input_args_names,
                        dbus_something.dbus_method.result_signature,
                        dbus_something.dbus_method.result_args_names,
                        dbus_something.dbus_method.flags,
                        dbus_something._call_from_dbus,
                    )
                elif isinstance(dbus_something, DbusPropertyBinded):
                    new_interface.add_property(
                        dbus_something.dbus_property.property_name,
                        dbus_something.dbus_property.property_signature,
                        dbus_something.get_sync,
                        dbus_something.set_sync
                        if dbus_something.dbus_property.
                        property_set is not None
                        else None,
                        dbus_something.dbus_property.flags,
                    )
                elif isinstance(dbus_something, DbusSignalBinded):
                    new_interface.add_signal(
                        dbus_something.dbus_signal.signal_name,
                        dbus_something.dbus_signal.signature,
                        dbus_something.dbus_signal.args_names,
                        dbus_something.dbus_signal.flags,
                    )
                else:
                    raise TypeError

            bus.add_interface(new_interface, object_path,
                              interface_name)
            self.activated_interfaces.append(new_interface)

    def _connect(
        self,
        bus: SdBus,
        service_name: str,
        object_path: str,
    ) -> None:

        self.is_binded = True
        self.attached_bus = bus
        self.remote_service_name = service_name
        self.remote_object_path = object_path

    @ classmethod
    def new_connect(
        cls: Type[T_input],
        bus: SdBus,
        service_name: str,
        object_path: str,
    ) -> T_input:

        new_object = cls.__new__(cls)
        assert isinstance(new_object, DbusInterfaceBase)
        new_object._connect(bus, service_name, object_path)
        assert isinstance(new_object, cls)
        return new_object


DbusMethodType = Callable[[DbusInterfaceBase, Any], Coroutine[Any, Any, Any]]


class DbusPeerInterface(DbusInterfaceBase,
                        interface_name='org.freedesktop.DBus.Peer',
                        serving_enabled=False,
                        ):

    @ dbus_method()
    async def ping(self) -> None:
        raise NotImplementedError

    @ dbus_method()
    async def get_machine_id(self) -> str:
        raise NotImplementedError


class DbusIntrospectable(DbusInterfaceBase,
                         interface_name='org.freedesktop.DBus.Introspectable',
                         serving_enabled=False,
                         ):

    @ dbus_method()
    async def introspect(self) -> str:
        raise NotImplementedError


class DbusInterfaceCommon(DbusPeerInterface, DbusIntrospectable):
    ...
