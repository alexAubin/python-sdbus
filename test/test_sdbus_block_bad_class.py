# SPDX-License-Identifier: LGPL-2.1-or-later

# Copyright (C) 2023 igo95862

# This file is part of python-sdbus

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

from unittest import SkipTest, TestCase
from unittest import main as unittest_main

from sdbus import DbusInterfaceCommon, dbus_method, dbus_property
from sdbus.sd_bus_internals import is_interface_name_valid


class GoodDbusInterface(DbusInterfaceCommon):
    @dbus_method()
    def test_method(self) -> None:
        raise NotImplementedError

    @dbus_property("s")
    def test_property(self) -> str:
        return "test"


class TestBadDbusClass(TestCase):
    def test_method_name_override(self) -> None:
        with self.subTest("Method override"), self.assertRaises(TypeError):

            class BadMethodOverrideClass(GoodDbusInterface):
                def test_method(self) -> None:
                    return

        with self.subTest("D-Bus method override"), self.assertRaises(
            TypeError
        ):

            class BadDbusMethodOverrideClass(GoodDbusInterface):
                @dbus_method()
                def test_method(self) -> None:
                    return

        with self.subTest("Property override"), self.assertRaises(TypeError):

            class BadPropertyOverrideClass(GoodDbusInterface):
                def test_property(self) -> str:  # type: ignore
                    return "override"

        with self.subTest("D-Bus property override"), self.assertRaises(
            TypeError
        ):

            class BadDbusPropertyOverrideClass(GoodDbusInterface):
                @dbus_property("s")
                def test_property(self) -> str:
                    return "override"

        with self.subTest("Good new method"):

            class GoodSubclass(GoodDbusInterface):
                def new_method(self) -> int:
                    return 1

    def test_bad_class_names(self) -> None:
        if not __debug__:
            raise SkipTest("Assertions are not enabled")

        try:
            is_interface_name_valid("org.test")
        except NotImplementedError:
            raise SkipTest("Validation functions not available")

        with self.assertRaisesRegex(AssertionError, "^Invalid interface name"):

            class BadInterfaceName(
                DbusInterfaceCommon,
                interface_name="0.test",
            ):
                ...

        with self.assertRaisesRegex(
            AssertionError,
            "^Invalid method name",
        ):

            class BadMethodName(
                DbusInterfaceCommon,
                interface_name="org.example",
            ):
                @dbus_method(
                    result_signature="s",
                    method_name="🤫",
                )
                def test(self) -> str:
                    return "test"

        with self.assertRaisesRegex(
            AssertionError,
            "^Invalid property name",
        ):

            class BadPropertyName(
                DbusInterfaceCommon,
                interface_name="org.example",
            ):
                @dbus_property(
                    property_signature="s",
                    property_name="🤫",
                )
                def test(self) -> str:
                    return "test"


if __name__ == "__main__":
    unittest_main()
