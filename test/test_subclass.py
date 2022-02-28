import tempfile
import torch
from copy import deepcopy
from torch import nn
from torch.nn.utils.parametrize import register_parametrization
from torch.nn.modules.lazy import LazyModuleMixin
from torch.testing._internal.common_utils import (
    TestCase, run_tests, parametrize, subtest, instantiate_parametrized_tests)
from torch.testing._internal.common_subclass import subclass_db
from unittest import expectedFailure


class TestSubclass(TestCase):
    def _create_tensor(self, tensor_cls):
        return subclass_db[tensor_cls].create_fn(3)

    @parametrize("tensor_cls", [subtest(tensor_cls, name=info.name) for tensor_cls, info in subclass_db.items()])
    @parametrize("as_param", [False, True])
    def test_deepcopy(self, tensor_cls, as_param):
        x = self._create_tensor(tensor_cls)
        if as_param:
            x = nn.Parameter(x)
        x_copy = deepcopy(x)
        self.assertEqual(x, x_copy)
        self.assertEqual(x.__class__, x_copy.__class__)
        self.assertFalse(x is x_copy)
        if as_param:
            # Deepcopy should preserve both custom type and "parameter-ness".
            self.assertTrue(isinstance(x_copy, tensor_cls))
            self.assertTrue(isinstance(x_copy, nn.Parameter))
        else:
            self.assertTrue(type(x_copy) is tensor_cls)

    @parametrize("tensor_cls", [subtest(tensor_cls, name=info.name) for tensor_cls, info in subclass_db.items()])
    @parametrize("as_param", [False, True])
    def test_serialization(self, tensor_cls, as_param):
        with tempfile.TemporaryFile() as f:
            x = self._create_tensor(tensor_cls)
            if as_param:
                x = nn.Parameter(x)
            torch.save(x, f)
            f.seek(0)
            x_loaded = torch.load(f)

            self.assertEqual(x, x_loaded)
            self.assertFalse(x is x_loaded)
            if as_param:
                # Serialization should preserve both custom type and "parameter-ness".
                self.assertTrue(isinstance(x_loaded, tensor_cls))
                self.assertTrue(isinstance(x_loaded, nn.Parameter))
            else:
                self.assertTrue(type(x_loaded) is tensor_cls)

    @parametrize("tensor_cls", [subtest(tensor_cls, name=info.name) for tensor_cls, info in subclass_db.items()])
    @parametrize("as_param", [False, True])
    def test_repr(self, tensor_cls, as_param):
        x = self._create_tensor(tensor_cls)
        if as_param:
            x = nn.Parameter(x)
        str_repr = x.__repr__()
        if tensor_cls is not torch.Tensor:
            self.assertTrue(tensor_cls.__name__ in str_repr)
        self.assertEqual(as_param, 'Parameter' in str_repr)

    @parametrize("tensor_cls", [subtest(tensor_cls, name=info.name) for tensor_cls, info in subclass_db.items()])
    @parametrize("as_param", [False, True])
    def test_type_propagation(self, tensor_cls, as_param):
        x = self._create_tensor(tensor_cls)
        if as_param:
            x = nn.Parameter(x)

        # Call the add operator to produce an output tensor.
        output = x + self._create_tensor(torch.Tensor)

        # Custom type should be propagated across operations, but "parameter-ness" should not be.
        self.assertTrue(output.__class__ is (tensor_cls if as_param else x.__class__))

    @parametrize("tensor_cls", [subtest(tensor_cls, name=info.name) for tensor_cls, info in subclass_db.items()])
    def test_module_optimization(self, tensor_cls):
        def create_fn():
            return self._create_tensor(tensor_cls)

        class MyModule(nn.Module):
            def __init__(self):
                super().__init__()
                self.p1 = nn.Parameter(create_fn())

                self.p_list = nn.ParameterList([create_fn() for _ in range(3)])
                self.p_list.append(create_fn())

                self.p_dict = nn.ParameterDict({
                    'foo': create_fn(),
                    'bar': create_fn(),
                })
                self.p_dict['baz'] = create_fn()

                with torch.no_grad():
                    nn.init.normal_(self.p1)
                    for p in self.p_list:
                        nn.init.uniform_(p)
                    for _, p in self.p_dict.items():
                        nn.init.uniform_(p)

            def forward(self, x):
                out = self.p1 + x
                for p in self.p_list:
                    out = p + out

                for _, v in self.p_dict.items():
                    out = v + out

                return out

        m = MyModule()
        self.assertEqual(len(m.state_dict()), 8)

        optimizer = torch.optim.SGD(m.parameters(), lr=0.1)
        m(create_fn()).sum().backward(torch.tensor(1))
        optimizer.step()

    @parametrize("tensor_cls", [subtest(tensor_cls, name=info.name) for tensor_cls, info in subclass_db.items()])
    def test_parametrization(self, tensor_cls):
        def create_fn():
            return self._create_tensor(tensor_cls)

        class MyModule(nn.Module):
            def __init__(self):
                super().__init__()
                self.weight = nn.Parameter(create_fn())

            def forward(self, x):
                return self.weight + x

        class MyParametrization(nn.Module):
            def forward(self, X):
                return -X

        m = MyModule()
        self.assertEqual(len(m.state_dict()), 1)
        register_parametrization(m, 'weight', MyParametrization())
        self.assertIs(type(m.weight), tensor_cls)
        output = m(self._create_tensor(torch.Tensor))
        self.assertIs(type(output), tensor_cls)

    # Lazy modules with custom tensors are not supported yet.
    @expectedFailure
    @parametrize("tensor_cls", [subtest(tensor_cls, name=info.name) for tensor_cls, info in subclass_db.items()])
    def test_lazy_module(self, tensor_cls):

        class MyLazyModule(LazyModuleMixin, nn.Module):
            def __init__(self):
                super().__init__()
                self.param = nn.UninitializedParameter()

            def initialize_parameters(self, input) -> None:  # type: ignore[override]
                if self.has_uninitialized_params():
                    with torch.no_grad():
                        self.param.materialize(input.shape)
                        nn.init.uniform_(self.param)

            def forward(self, x):
                return self.param + x

        m = MyLazyModule()
        self.assertTrue(m.has_uninitialized_params())
        output = m(self._create_tensor(tensor_cls))
        self.assertFalse(m.has_uninitialized_params())
        self.assertIs(type(m.param), tensor_cls)

instantiate_parametrized_tests(TestSubclass)

if __name__ == '__main__':
    run_tests()
