// SPDX-License-Identifier: MIT
const chatApiHandler = jest.fn();
const getNemoAgentToolkitSSProps = jest.fn().mockResolvedValue({ props: {} });

module.exports = { chatApiHandler, getNemoAgentToolkitSSProps };
