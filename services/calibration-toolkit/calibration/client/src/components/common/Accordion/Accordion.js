// SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { Component } from 'react';
import './Accordion.css';
import { Divider, Icon } from 'semantic-ui-react';

class MultipleAccordion extends Component {
  constructor(props) {
    super(props)
    this.state = {
        activePanel: ['option1'],
    }
}

  handleSelect = (option) => {
      let selectedOptions = this.state.activePanel
      if (selectedOptions.includes(option)) {
        let position = selectedOptions.indexOf(option)
        selectedOptions.splice(position, 1)
      } else {
        selectedOptions.push(option)
      }
      this.setState({activePanel : selectedOptions})
  }

  render() {
      let options = [
        {title: 'option1', content: "Lorem Ipsum is simply dummy text of the printing and typesetting industry."},
        {title: 'option2', content: "Lorem Ipsum has been the industry's standard dummy text."},
        {title: 'option3', content: "Lorem Ipsum is simply dummy text of the printing and typesetting industry. Lorem Ipsum has been the industry's standard dummy text."}
      ]
      let selectedOptions = this.state.activePanel
      
    return (
        <div className="multiple-accordion">
            {options.map(option => {
                return (
                    <div className="panel" key={option.title}
                        onClick={() => this.handleSelect(option.title)}
                    >
                        <span>{option.title}</span>
                        <Icon name={selectedOptions.indexOf(option.title) > -1 ? "angle up" : "angle down" }/>
                        {selectedOptions.indexOf(option.title) > -1 ? 
                            <div className="accordion-content">{option.content}</div> : <div></div>}
                    </div>
                )
            })}
        </div>
    )
  }
}

class SingleAccordion extends Component {
    constructor(props) {
        super(props)
        this.state = {
            active: false,
        }
    }
    render() {
        let content;
        if (this.state.active) {
          content = (
              <div className="accordion-content">Lorem Ipsum is simply dummy text of the printing and typesetting industry. Lorem Ipsum has been the industry's standard dummy text.</div>
          )
        } else {
          content = (
              <div></div>
          )
        }
      return (
            <div className="panel"
            onClick={() => this.setState({ active:!this.state.active })}
            >
                <span>option1</span>
                <Icon name={this.state.active ? "angle up" : "angle down" }/>
                {content}
            </div>
      )
    }
}

class Accordion extends Component {
  render() {
    return (
        <div>
            <p>Single Accordion</p>
            <SingleAccordion/>
            <Divider hidden />
            <p>Multiple Accordion</p>
            <MultipleAccordion/>
        </div>
    )
  }
}

export default Accordion;
